from fastapi import APIRouter,Depends,WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session
from services.gpu_terminal import serve

router=APIRouter()


@router.websocket('/ws')
async def terminal(ws:WebSocket,db:AsyncSession=Depends(get_db_session)):
    await serve(ws,db)
