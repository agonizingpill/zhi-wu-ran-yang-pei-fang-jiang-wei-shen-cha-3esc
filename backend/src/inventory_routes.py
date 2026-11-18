from fastapi import APIRouter,Depends
from sqlalchemy import select
from .database import session
from .auth import current,editor,admin
from .inventory_models import StockCount,CountAnomalyModel
from .inventory_schemas import CountInput,CountVerdictInput
from . import inventory_service
router=APIRouter()
@router.get('/stock-counts')
def counts(user=Depends(current),db=Depends(session)):
    return db.scalars(select(StockCount).order_by(StockCount.id.desc())).all()
@router.post('/stock-counts')
def register_count(p:CountInput,user=Depends(editor),db=Depends(session)):
    return inventory_service.register(db,p,user)
@router.post('/stock-counts/{key}/verdict')
def verdict_count(key:int,p:CountVerdictInput,user=Depends(admin),db=Depends(session)):
    return inventory_service.verdict(db,key,p,user)
@router.get('/count-anomaly-models')
def anomaly_models(user=Depends(current),db=Depends(session)):
    return inventory_service.list_models(db)
