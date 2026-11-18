from datetime import date
from typing import Literal
from pydantic import Field
from .fulfillment_schemas import Inputs

class CountInput(Inputs):
    code:str=Field(min_length=1,max_length=50)
    lot_id:int=Field(gt=0)
    counted_on:date
    measured_kg:float=Field(ge=0,le=10000)
    note:str=Field(default='',max_length=400)

class CountVerdictInput(Inputs):
    verdict:Literal['normal_loss','abnormal_shortage','rejected']
    note:str=Field(min_length=5,max_length=400)
