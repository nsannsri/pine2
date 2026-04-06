"""
Database models for GEX Dashboard.
SQLite + SQLAlchemy with 7-day rolling cleanup.
"""

import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DB_PATH = os.path.join(os.path.dirname(__file__), "gex_dashboard.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Session = sessionmaker(bind=engine)
Base = declarative_base()

RETENTION_DAYS = 7


class Price(Base):
    """1-minute OHLCV price data from Dhan."""
    __tablename__ = "prices"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        Index("idx_prices_symbol_ts", "symbol", "timestamp", unique=True),
    )


class GexSnapshot(Base):
    """GEX levels snapshot taken every 1 minute."""
    __tablename__ = "gex_snapshots"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    expiry = Column(String(20))
    spot = Column(Float)

    # Key levels
    call_wall = Column(Float)
    put_wall = Column(Float)
    hvl = Column(Float)
    local_flip = Column(Float)
    peak_gamma = Column(Float)
    max_pain = Column(Float)

    # Bias & regime
    bias = Column(String(20))
    bias_score = Column(Integer)
    gamma_condition = Column(String(20))
    gamma_tilt = Column(Float)
    regime = Column(String(20))
    regime_score = Column(Integer)

    # GEX values
    net_gex = Column(Float)
    call_gex = Column(Float)
    put_gex = Column(Float)

    # Expected move
    em_upper = Column(Float)
    em_lower = Column(Float)
    em_wk_upper = Column(Float)
    em_wk_lower = Column(Float)
    atm_iv = Column(Float)
    dte = Column(Integer)

    # IV Skew
    iv_skew = Column(Float)
    skew_signal = Column(String(20))

    # OI Change
    total_ce_oi_chg = Column(Integer)
    total_pe_oi_chg = Column(Integer)
    net_oi_chg_direction = Column(String(20))

    # XAUUSD conversion (GOLD only)
    xau_spot = Column(Float)
    xau_call_wall = Column(Float)
    xau_put_wall = Column(Float)
    xau_peak_gamma = Column(Float)
    xau_max_pain = Column(Float)
    xau_local_flip = Column(Float)
    xau_em_upper = Column(Float)
    xau_em_lower = Column(Float)
    usdinr = Column(Float)

    # Relationships
    strikes = relationship("StrikeGex", back_populates="snapshot", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_gex_symbol_ts", "symbol", "timestamp", unique=True),
    )


class StrikeGex(Base):
    """Per-strike GEX data for each snapshot."""
    __tablename__ = "strike_gex"

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("gex_snapshots.id", ondelete="CASCADE"), nullable=False)
    strike = Column(Float, nullable=False)
    ce_oi = Column(Integer)
    pe_oi = Column(Integer)
    ce_gex = Column(Float)
    pe_gex = Column(Float)
    net_gex = Column(Float)
    ce_iv = Column(Float)
    pe_iv = Column(Float)

    snapshot = relationship("GexSnapshot", back_populates="strikes")

    __table_args__ = (
        Index("idx_strike_snapshot", "snapshot_id", "strike"),
    )


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def cleanup_old_data():
    """Remove data older than RETENTION_DAYS."""
    session = Session()
    try:
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)

        # Delete old strike data (cascades from snapshots)
        old_snapshots = session.query(GexSnapshot).filter(GexSnapshot.timestamp < cutoff).all()
        for snap in old_snapshots:
            session.delete(snap)

        # Delete old prices
        session.query(Price).filter(Price.timestamp < cutoff).delete()

        session.commit()
        deleted = len(old_snapshots)
        if deleted > 0:
            print(f"[DB] Cleaned up {deleted} old snapshots (>{RETENTION_DAYS} days)")
    except Exception as e:
        session.rollback()
        print(f"[DB] Cleanup error: {e}")
    finally:
        session.close()


def get_session():
    """Get a new DB session."""
    return Session()


# Initialize on import
init_db()
