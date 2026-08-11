"""No monetary column may be a floating-point type (ADR-003).

The cheapest possible guard against the highest-cost class of bug in a financial
product. It sweeps every mapped table, so it covers models that do not exist
yet -- which is the point of writing it in M0.
"""

from __future__ import annotations

from sqlalchemy import Float

from app.core.models import Base

# Importing every module's models registers them on Base.metadata. Each
# milestone appends its import here, and the sweep below then covers the new
# tables automatically:
#   M1  from app.modules.auth import models as _auth
#   M2  from app.modules.finance import models as _finance


def test_no_table_declares_a_float_column():
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Float)
    ]
    assert not offenders, (
        f"Floating-point columns found: {offenders}. Money must be NUMERIC(18,2) "
        "and probabilities NUMERIC(4,3) -- see ADR-003."
    )
