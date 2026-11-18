"""按染料组分学习盘点盘亏的正常损耗分布。

证据只来自主管已裁定的历史盘点：特征为盘亏率（盘亏 kg / 登记时账存 kg），
标签区分正常损耗与异常短缺。样本不足或统计量退化时不输出概率，由主管自行裁定。
纯标准库实现，便于离线复核；与 atlas.py 一样不依赖在线训练服务。
"""
import math

MIN_SAMPLES = 5
MIN_VARIANCE = 1e-12

def _rate(shortage_kg, book_kg):
    # 零账存时盘亏率没有定义，这类样本只计数、不进分布拟合
    if book_kg <= 1e-9:
        return None
    return max(0.0, shortage_kg) / book_kg

def build_training_rows(counts):
    """counts: 已裁定盘点（含 lot 组分信息的字典列表），返回分组训练依据。"""
    groups = {'A': [], 'B': [], 'C': []}
    for c in counts:
        if c['state'] not in ('normal_loss', 'abnormal_shortage'):
            continue
        rate = _rate(c['shortage_kg'], c['book_kg'])
        row = {
            'count_id': c['id'], 'lot_code': c.get('lot_code', ''),
            'book_kg': round(c['book_kg'], 6), 'measured_kg': round(c['measured_kg'], 6),
            'consumed_kg': round(c['consumed_kg'], 6), 'shortage_kg': round(c['shortage_kg'], 6),
            'shortage_rate': None if rate is None else round(rate, 6),
            'label': c['state'],
        }
        groups.setdefault(c['component'], []).append(row)
    return groups

def _normal_cdf(x, mean, std):
    return 0.5 * (1.0 + math.erf((x - mean) / (std * math.sqrt(2.0))))

def fit_component(rows):
    """用正常损耗样本拟合盘亏率均值/标准差；证据不足返回 None。"""
    normal = [r['shortage_rate'] for r in rows if r['label'] == 'normal_loss' and r['shortage_rate'] is not None]
    if len(normal) < MIN_SAMPLES:
        return None
    mean = sum(normal) / len(normal)
    variance = sum((x - mean) ** 2 for x in normal) / (len(normal) - 1)
    if variance < MIN_VARIANCE:
        # 历史损耗完全一致时，任何超出该值的盘亏都视为缺乏分布依据，不报概率
        return None
    std = math.sqrt(variance)
    # 经验分位阈值：正常损耗样本的最大盘亏率，超过即落进历史未见区间
    return {'mean': mean, 'std': std, 'normal_samples': len(normal),
            'upper_observed': max(normal), 'threshold_rate': mean + 2 * std}

def evaluate(rows, params):
    """回判检验：用拟合分布给每条历史样本打分，输出混淆矩阵与检验表现。"""
    tp = fp = tn = fn = 0
    checked = []
    for r in rows:
        if r['shortage_rate'] is None:
            continue
        p = anomaly_probability(r['shortage_rate'], params)
        # 检验判定按 2σ 阈值，而不是概率 0.5，避免把高于均值的正常损耗误报
        predicted_anomaly = p is not None and r['shortage_rate'] > params['threshold_rate']
        actual_anomaly = r['label'] == 'abnormal_shortage'
        if predicted_anomaly and actual_anomaly:
            tp += 1
        elif predicted_anomaly and not actual_anomaly:
            fp += 1
        elif not predicted_anomaly and actual_anomaly:
            fn += 1
        else:
            tn += 1
        checked.append({'count_id': r['count_id'], 'label': r['label'],
                        'probability': None if p is None else round(p, 4),
                        'predicted': 'abnormal_shortage' if predicted_anomaly else 'normal_loss'})
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {'confusion': {'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn},
            'precision': None if precision is None else round(precision, 4),
            'recall': None if recall is None else round(recall, 4),
            'checked': checked}

def anomaly_probability(rate, params):
    """盘亏率相对正常损耗分布的异常概率：CDF(z)，损耗越高于正常均值概率越接近 1。"""
    if rate is None or not params:
        return None
    z = (rate - params['mean']) / params['std']
    return min(1.0, max(0.0, _normal_cdf(z, 0.0, 1.0)))

def train(counts):
    """返回每个组分的模型参数、训练依据与检验表现；证据不足的组分不建模。"""
    groups = build_training_rows(counts)
    models = {}
    for component, rows in groups.items():
        params = fit_component(rows)
        if params is None:
            models[component] = {'ready': False, 'sample_size': len(rows),
                                 'reason': '正常损耗样本不足%d条或分布退化，不输出概率' % MIN_SAMPLES,
                                 'training_evidence': rows, 'validation': None, 'params': None}
            continue
        validation = evaluate(rows, params)
        models[component] = {'ready': True, 'sample_size': len(rows),
                             'training_evidence': rows, 'validation': validation, 'params': params}
    return models

def assess(component_models, component, shortage_kg, book_kg):
    """登记盘点时调用：返回 (probability|None, evidence dict)。"""
    rate = _rate(shortage_kg, book_kg)
    m = component_models.get(component)
    if rate is None or not m or not m.get('ready'):
        reason = '账存为零无法计算盘亏率' if rate is None else (
            m.get('reason', '该组分证据不足，不输出概率') if m else '该组分尚无训练模型，不输出概率')
        return None, {'ready': False, 'reason': reason, 'shortage_rate': rate}
    p = anomaly_probability(rate, m['params'])
    return p, {'ready': True, 'shortage_rate': round(rate, 6),
               'mean_rate': round(m['params']['mean'], 6), 'std_rate': round(m['params']['std'], 6),
               'threshold_rate': round(m['params']['threshold_rate'], 6),
               'normal_samples': m['params']['normal_samples'],
               'beyond_history': rate > m['params']['upper_observed'],
               'note': '概率仅为参考，异常与否由主管裁定'}
