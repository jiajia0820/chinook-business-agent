"""Compare only explicitly aligned scalar claims; targets are not actuals."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ScalarClaim:
    metric: str
    entity: str
    period: str
    unit: str
    scope: tuple
    role: str
    value: Decimal
    source: str


def compare_claims(left, right):
    keys=('metric','entity','period','unit','scope')
    different=[key for key in keys if getattr(left,key)!=getattr(right,key)]
    if different: return {'status':'not_comparable','differences':different}
    if left.role!=right.role: return {'status':'business_gap','difference':str(left.value-right.value)}
    if left.value!=right.value:
        return {'status':'conflict','difference':str(left.value-right.value),'sources':[left.source,right.source]}
    return {'status':'consistent'}
