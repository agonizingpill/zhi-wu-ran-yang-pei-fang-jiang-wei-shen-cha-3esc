from datetime import datetime
from sqlalchemy import select,func
from fastapi import HTTPException
from .fulfillment_models import DyeLot,DyeIssue
from .inventory_models import StockCount,CountAnomalyModel
from .inventory_schemas import CountInput,CountVerdictInput
from . import inventory_ml
from .recipe_service import log

ADJUSTED_STATES=('normal_loss','abnormal_shortage')

def get_count(db,key):
    item=db.scalar(select(StockCount).where(StockCount.id==key).with_for_update())
    if not item:raise HTTPException(404,'盘点单不存在')
    return item

def _newer_adjusted_exists(db,lot_id,counted_on,exclude_id=None):
    q=select(StockCount.id).where(
        StockCount.lot_id==lot_id,StockCount.state.in_(ADJUSTED_STATES),
        StockCount.counted_on>=counted_on)
    if exclude_id is not None:q=q.where(StockCount.id!=exclude_id)
    return db.scalar(q) is not None

def _assess(db,component,shortage_kg,book_kg):
    model=db.scalar(select(CountAnomalyModel).where(CountAnomalyModel.component==component))
    component_models={component:{'ready':bool(model and model.params),'params':model.params if model else None,
                                 'reason':'该组分证据不足，不输出概率'}}
    return inventory_ml.assess(component_models,component,shortage_kg,book_kg)

def register(db,p:CountInput,user):
    lot=db.scalar(select(DyeLot).where(DyeLot.id==p.lot_id).with_for_update())
    if not lot:raise HTTPException(404,'染料批次不存在')
    if db.scalar(select(StockCount.id).where(StockCount.code==p.code)):raise HTTPException(409,'盘点单号已存在')
    if db.scalar(select(StockCount.id).where(StockCount.lot_id==lot.id,StockCount.counted_on==p.counted_on)):
        raise HTTPException(409,'该染料批次当日已登记盘点，每批次每日仅允许一张盘点单')
    # 同批次更新的盘点已调账后，旧盘点拒绝入账，防止用过期实盘冲掉已确认结果
    if _newer_adjusted_exists(db,lot.id,p.counted_on):
        raise HTTPException(409,'同批次已有更新盘点完成调账，旧盘点不能入账')
    # 登记瞬间固定账存；称料消耗只作为历史依据快照，不阻止盘点进行
    book_kg=round(lot.available_kg,6)
    consumed=db.scalar(select(func.coalesce(func.sum(DyeIssue.kg),0.0)).where(DyeIssue.lot_id==lot.id)) or 0.0
    shortage_kg=round(book_kg-p.measured_kg,6)
    probability,evidence=_assess(db,lot.component,shortage_kg,book_kg)
    item=StockCount(code=p.code,lot_id=lot.id,counted_on=p.counted_on,book_kg=book_kg,
                    measured_kg=p.measured_kg,note=p.note,consumed_kg=round(float(consumed),6),shortage_kg=shortage_kg,
                    state='pending',anomaly_probability=probability,probability_evidence=evidence,
                    created_by=user.email)
    db.add(item);db.flush()
    log(db,user,f'登记盘点 {p.code} 批次{lot.code} 账存{book_kg}kg 实盘{p.measured_kg}kg'
                +('' if probability is None else f' 异常参考概率{probability:.3f}（待主管裁定）'))
    db.commit();return item

def _training_rows(db):
    rows=db.execute(select(StockCount,DyeLot).join(DyeLot,DyeLot.id==StockCount.lot_id)
                    .where(StockCount.state.in_(ADJUSTED_STATES))).all()
    return [{'id':c.id,'component':lot.component,'lot_code':lot.code,'book_kg':c.book_kg,
             'measured_kg':c.measured_kg,'consumed_kg':c.consumed_kg,'shortage_kg':c.shortage_kg,
             'state':c.state} for c,lot in rows]

def _retrain(db,trigger_count,user):
    # 按组分用全部已裁定历史盘点重新学习；证据不足的组分保留“未就绪”状态，不输出概率
    fitted=inventory_ml.train(_training_rows(db))
    for component,payload in fitted.items():
        model=db.scalar(select(CountAnomalyModel).where(CountAnomalyModel.component==component).with_for_update())
        if not model:
            model=CountAnomalyModel(component=component);db.add(model)
        model.sample_size=payload['sample_size']
        model.training_evidence={'rows':payload['training_evidence'],
                                 'reason':payload.get('reason')}
        model.validation=payload['validation'] or {}
        model.params=payload['params'] or {}
        model.trained_count_id=trigger_count.id
        model.trained_by=user.email

def verdict(db,key,p:CountVerdictInput,user):
    item=get_count(db,key)
    if item.state!='pending':raise HTTPException(409,'盘点单已裁定，调账不可重复执行')
    lot=db.scalar(select(DyeLot).where(DyeLot.id==item.lot_id).with_for_update())
    if not lot:raise HTTPException(404,'染料批次不存在')
    if _newer_adjusted_exists(db,lot.id,item.counted_on,exclude_id=item.id):
        raise HTTPException(409,'同批次更新盘点已调账，本张旧盘点拒绝入账')
    item.verdict_note=p.note;item.verdict_by=user.email;item.adjusted_at=datetime.utcnow()
    if p.verdict=='rejected':
        item.state='rejected'
        log(db,user,f'盘点 {item.code} 主管驳回，不调整库存')
        db.commit();return item
    # 调账差额永远取登记时冻结的账存/实盘；盘点后的称料领料保留在当前余额中
    delta=round(item.measured_kg-item.book_kg,6)
    before=round(lot.available_kg,6);after=round(before+delta,6)
    if after<-1e-8:
        raise HTTPException(409,f'调账后余额为负（当前{before}kg，差额{delta}kg），整单拒绝，请先核对盘点后的领料')
    lot.available_kg=after
    item.state=p.verdict;item.adjusted_kg=delta
    item.balance_before=before;item.balance_after=after
    log(db,user,f'盘点 {item.code} 裁定{p.verdict} 按冻结差额{delta}kg调账 {lot.code} 余额{before}→{after}kg')
    _retrain(db,item,user)
    db.commit();return item

def list_models(db):
    return db.scalars(select(CountAnomalyModel).order_by(CountAnomalyModel.component)).all()
