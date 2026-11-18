from sqlalchemy import String,Float,Integer,ForeignKey,JSON,Date,DateTime,CheckConstraint,UniqueConstraint,func
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base

class StockCount(Base):
    # 盘点登记时固定账存与实盘，后续称料照常进行；主管按登记时差额调账，盘点后的领料保留在余额里。
    __tablename__='stock_counts'
    __table_args__=(
        UniqueConstraint('lot_id','counted_on',name='uq_stock_count_lot_day'),
        CheckConstraint("state IN ('pending','normal_loss','abnormal_shortage','rejected')"),
        CheckConstraint('measured_kg>=0 AND book_kg>=0'),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(50),unique=True)
    lot_id:Mapped[int]=mapped_column(ForeignKey('dye_lots.id'))
    counted_on:Mapped[object]=mapped_column(Date)
    # 登记瞬间冻结的账面与实盘，之后称料不再改写这两个值
    book_kg:Mapped[float]=mapped_column(Float)
    measured_kg:Mapped[float]=mapped_column(Float)
    note:Mapped[str]=mapped_column(String(400),default='')
    consumed_kg:Mapped[float]=mapped_column(Float,default=0.0)
    shortage_kg:Mapped[float]=mapped_column(Float,default=0.0)
    state:Mapped[str]=mapped_column(String(24),default='pending')
    # 登记时按该批次历史模型给出的异常参考；证据不足时为 None，不输出概率
    anomaly_probability:Mapped[float|None]=mapped_column(Float,nullable=True)
    probability_evidence:Mapped[dict]=mapped_column(JSON,default=dict)
    adjusted_kg:Mapped[float|None]=mapped_column(Float,nullable=True)
    balance_before:Mapped[float|None]=mapped_column(Float,nullable=True)
    balance_after:Mapped[float|None]=mapped_column(Float,nullable=True)
    verdict_note:Mapped[str]=mapped_column(String(400),default='')
    verdict_by:Mapped[str]=mapped_column(String(120),default='')
    adjusted_at:Mapped[object|None]=mapped_column(DateTime,nullable=True)
    created_by:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[object]=mapped_column(DateTime,server_default=func.now())

class CountAnomalyModel(Base):
    # 按染料组分分组，从已裁定的历史盘点（账存、实盘、称料消耗、盘亏）学习正常损耗分布。
    __tablename__='count_anomaly_models'
    __table_args__=(UniqueConstraint('component'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    component:Mapped[str]=mapped_column(String(1),unique=True)
    sample_size:Mapped[int]=mapped_column(Integer,default=0)
    # 训练依据快照：每条历史盘点的账存/实盘/消耗/盘亏与裁定标签
    training_evidence:Mapped[dict]=mapped_column(JSON,default=dict)
    # 检验表现：对训练样本回判的混淆矩阵、召回与误报
    validation:Mapped[dict]=mapped_column(JSON,default=dict)
    params:Mapped[dict]=mapped_column(JSON,default=dict)
    trained_count_id:Mapped[int|None]=mapped_column(ForeignKey('stock_counts.id'),nullable=True)
    trained_by:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[object]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[object]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
