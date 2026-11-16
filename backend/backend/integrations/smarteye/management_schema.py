"""Durable management state; platform credentials remain in smarteye.platforms."""
from sqlalchemy import Column, Integer, JSON, String, Table
from integrations.smarteye.runtime import metadata

platform_status = Table('smarteye_platform_status', metadata,
    Column('tenant_id', Integer, primary_key=True),
    Column('plat_id', String(160), primary_key=True),
    Column('payload', JSON, nullable=False))

devices = Table('smarteye_device_registry', metadata,
    Column('tenant_id', Integer, primary_key=True),
    Column('plat_id', String(160), primary_key=True),
    Column('device_id', String(160), primary_key=True),
    Column('payload', JSON, nullable=False))

capture_schedules = Table('smarteye_capture_schedules', metadata,
    Column('sched_id', String(160), primary_key=True),
    Column('tenant_id', Integer, nullable=False),
    Column('payload', JSON, nullable=False))
