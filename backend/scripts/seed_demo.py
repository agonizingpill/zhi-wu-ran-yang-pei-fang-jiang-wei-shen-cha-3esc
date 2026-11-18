from datetime import date,datetime
from src.fulfillment_models import DyeLot,DyeIssue,ColorDispatch,ShadeComplaint
from src.inventory_models import StockCount,CountAnomalyModel
from src import inventory_ml
import math,random
from sqlalchemy import select,func
from src.database import Base,engine,Session
from src.models import User,DyeRecipe,Swatch,Trial
from src.production_models import RecipeRevision,DyeBatch
from src.auth import hash_password

def _seed_stock_counts(db):
    """历史盘点训练依据：A/B 组分正常损耗样本充足可建模，C 组分证据不足不报概率。"""
    trial=db.scalar(select(Trial).where(Trial.recipe_id==db.scalar(select(DyeRecipe.id).where(DyeRecipe.code=='DY-PROD')),Trial.state=='approved'))
    hist_lots=[]
    for code,component,available in [('HIST-A-01','A',42.0),('HIST-B-01','B',18.0),('HIST-C-01','C',9.0)]:
        lot=db.scalar(select(DyeLot).where(DyeLot.code==code))
        if not lot:
            lot=DyeLot(code=code,component=component,available_kg=available,expires_on=date(2027,12,31),state='active');db.add(lot);db.flush()
        hist_lots.append(lot)
    snapshot={'code':'DY-PROD','name':'量产靛蓝基准','id':trial.recipe_id,'dye_a':1.8,'dye_b':.4,'dye_c':.2}
    # 六个历史缸各 100kg，留下称料消耗凭据（A1.8/B0.4/C0.2 kg 每缸）
    for n in range(1,7):
        bcode=f'DB-HIST-{n:02}'
        batch=db.scalar(select(DyeBatch).where(DyeBatch.code==bcode))
        if not batch:
            batch=DyeBatch(code=bcode,approved_trial_id=trial.id,kg=100,target_snapshot={'lot':'PROD-REF','recipe_id':trial.recipe_id,'l':48,'a':-2,'b':-25,'trial_delta_e':.574456,'recipe_snapshot':snapshot},state='released',released_by='admin@qise.test');db.add(batch);db.flush()
        for lot in hist_lots:
            kg=1.8 if lot.component=='A' else .4 if lot.component=='B' else .2
            if not db.scalar(select(DyeIssue.id).where(DyeIssue.batch_id==batch.id,DyeIssue.lot_id==lot.id)):
                db.add(DyeIssue(batch_id=batch.id,lot_id=lot.id,component=lot.component,kg=kg,recipe_snapshot=snapshot,issued_by='colorist@qise.test'))
    db.flush()
    # (组分, 序号, 月份, 登记账存, 实盘, 裁定)；正常损耗约 0.4%~1.6%，异常短缺 8% 以上
    plan={
        'A':[(1,5,60.0,59.4,'normal_loss'),(2,6,54.0,53.5,'normal_loss'),(3,7,48.0,47.2,'normal_loss'),(4,8,42.0,41.6,'normal_loss'),(5,9,36.0,35.5,'normal_loss'),(6,10,30.0,27.4,'abnormal_shortage')],
        'B':[(1,5,25.0,24.8,'normal_loss'),(2,6,22.6,22.3,'normal_loss'),(3,7,20.2,20.0,'normal_loss'),(4,8,17.8,17.5,'normal_loss'),(5,9,15.4,15.2,'normal_loss'),(6,10,13.0,11.9,'abnormal_shortage')],
        'C':[(1,7,12.0,11.9,'normal_loss'),(2,8,10.8,10.7,'normal_loss'),(3,9,9.6,8.7,'abnormal_shortage')],
    }
    lot_by_component={lot.component:lot for lot in hist_lots}
    issue_sum={lot.id:(db.scalar(select(func.coalesce(func.sum(DyeIssue.kg),0.0)).where(DyeIssue.lot_id==lot.id)) or 0.0) for lot in hist_lots}
    for component,rows in plan.items():
        lot=lot_by_component[component]
        for seq,month,book,measured,state in rows:
            code=f'CNT-{component}-2026{month:02d}-{seq:02d}'
            if db.scalar(select(StockCount.id).where(StockCount.code==code)):continue
            shortage=round(book-measured,6);consumed=round(issue_sum[lot.id],6)
            db.add(StockCount(code=code,lot_id=lot.id,counted_on=date(2026,month,20),book_kg=book,measured_kg=measured,
                              consumed_kg=consumed,shortage_kg=shortage,state=state,
                              adjusted_kg=round(measured-book,6),balance_before=book,balance_after=measured,
                              verdict_note='历史盘点裁定：正常工艺损耗' if state=='normal_loss' else '历史盘点裁定：超出损耗区间的异常短缺',
                              verdict_by='admin@qise.test',adjusted_at=datetime(2026,month,20,16,0),created_by='colorist@qise.test'))
    db.flush()
    # 用已裁定历史训练分组分模型，并写回训练依据与检验表现
    fitted=inventory_ml.train(_count_training_rows(db))
    for component,payload in fitted.items():
        model=db.scalar(select(CountAnomalyModel).where(CountAnomalyModel.component==component))
        if not model:
            model=CountAnomalyModel(component=component);db.add(model)
        model.sample_size=payload['sample_size'];model.training_evidence={'rows':payload['training_evidence'],'reason':payload.get('reason')}
        model.validation=payload['validation'] or {};model.params=payload['params'] or {};model.trained_by='seed'
    db.flush()
    # 一张待主管裁定的演示盘点：A 组分模型就绪，给出右尾概率参考；裁定权仍在主管
    lot_a=db.scalar(select(DyeLot).where(DyeLot.code=='DYE-A-1117'))
    if not db.scalar(select(StockCount.id).where(StockCount.code=='CNT-A-1117-PEND')):
        book=lot_a.available_kg;measured=round(book*0.91,6);shortage=round(book-measured,6)
        probability,evidence=inventory_ml.assess({'A':{'ready':fitted['A']['ready'],'params':fitted['A']['params'],'reason':fitted['A'].get('reason')}},'A',shortage,book)
        db.add(StockCount(code='CNT-A-1117-PEND',lot_id=lot_a.id,counted_on=date(2026,11,17),book_kg=book,measured_kg=measured,
                          consumed_kg=0.0,shortage_kg=shortage,state='pending',anomaly_probability=probability,
                          probability_evidence=evidence,created_by='colorist@qise.test'))

def _count_training_rows(db):
    rows=db.execute(select(StockCount,DyeLot).join(DyeLot,DyeLot.id==StockCount.lot_id).where(StockCount.state.in_(('normal_loss','abnormal_shortage')))).all()
    return [{'id':c.id,'component':lot.component,'lot_code':lot.code,'book_kg':c.book_kg,'measured_kg':c.measured_kg,
             'consumed_kg':c.consumed_kg,'shortage_kg':c.shortage_kg,'state':c.state} for c,lot in rows]

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ['admin','colorist','viewer']:
            email=f'{role}@qise.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password=hash_password('Dye2026!'),role=role))
        recipes=[('DY-021','靛蓝亚麻',1.8,.4,.2),('DY-022','苔绿棉麻',.3,1.7,.7),('DY-023','赤陶平纹',.8,.3,1.5)]
        for code,name,a,b,c in recipes:
            if not db.scalar(select(DyeRecipe).where(DyeRecipe.code==code)):db.add(DyeRecipe(code=code,name=name,dye_a=a,dye_b=b,dye_c=c))
        db.flush();rr=db.scalars(select(DyeRecipe).order_by(DyeRecipe.id)).all();rng=random.Random(42)
        for i in range(60):
            lot=['REF-021','SAMPLE-021','SAMPLE-OFF'][i] if i<3 else f'SW-{i:03}'
            if db.scalar(select(Swatch).where(Swatch.lot==lot)):continue
            group=0 if i<3 else i%3
            l,a,b=(48,-2,-25) if i==0 else (48.7,-1.5,-24.5) if i==1 else (62,8,-14) if i==2 else (48+group*9+rng.uniform(-2,2),-2+group*6+rng.uniform(-1,1),-25+group*15+rng.uniform(-1,1))
            spectrum=[round(min(.95,max(.03,.26+group*.11+.1*math.sin(j/5+group)+rng.gauss(0,.012))),5) for j in range(36)]
            db.add(Swatch(recipe_id=rr[group].id,lot=lot,spectrum=spectrum,l=l,a=a,b=b,measured=True))
        prod=db.scalar(select(DyeRecipe).where(DyeRecipe.code=='DY-PROD'))
        if not prod:
            prod=DyeRecipe(code='DY-PROD',name='量产靛蓝基准',dye_a=1.8,dye_b=.4,dye_c=.2,locked=True);db.add(prod);db.flush()
        for lot,l,a,b in [('PROD-REF',48,-2,-25),('PROD-OK',48.5,-1.8,-24.8)]:
            if not db.scalar(select(Swatch).where(Swatch.lot==lot)):db.add(Swatch(recipe_id=prod.id,lot=lot,spectrum=[.3]*36,l=l,a=a,b=b))
        db.flush();ref=db.scalar(select(Swatch).where(Swatch.lot=='PROD-REF'));candidate=db.scalar(select(Swatch).where(Swatch.lot=='PROD-OK'))
        trial=db.scalar(select(Trial).where(Trial.recipe_id==prod.id,Trial.state=='approved'))
        if not trial:
            trial=Trial(recipe_id=prod.id,reference_swatch_id=ref.id,candidate_swatch_id=candidate.id,delta_e=.574456,state='approved',note='量产演示基准');db.add(trial);db.flush()
        if not db.scalar(select(DyeBatch).where(DyeBatch.code=='DB-DEMO-001')):db.add(DyeBatch(code='DB-DEMO-001',approved_trial_id=trial.id,kg=250,target_snapshot={'lot':ref.lot,'recipe_id':prod.id,'l':ref.l,'a':ref.a,'b':ref.b,'trial_delta_e':trial.delta_e}))
        for code,component,kg,expiry,state in [('DYE-A-1117','A',80,date(2027,12,31),'active'),('DYE-B-1117','B',40,date(2027,12,31),'active'),('DYE-C-1117','C',20,date(2027,12,31),'active'),('DYE-C-HOLD','C',5,date(2027,12,31),'quarantine'),('DYE-B-OLD','B',5,date(2026,1,1),'active')]:
            if not db.scalar(select(DyeLot).where(DyeLot.code==code)):db.add(DyeLot(code=code,component=component,available_kg=kg,expires_on=expiry,state=state))
        db.flush()
        _seed_stock_counts(db)
        db.commit()
        print({name:db.scalar(select(func.count()).select_from(model)) for name,model in [('users',User),('recipes',DyeRecipe),('swatches',Swatch)]})
if __name__=='__main__':seed()
