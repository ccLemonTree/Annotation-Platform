import asyncio
from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from schemas.model_service import ModelServiceConfig
from services.model_inference import infer


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol,kind,payload',[
    ('model_service','detection',{'detections':[]}),
    ('openai_chat','caption',{'choices':[{'message':{'content':'a person'}}]}),
    ('openai_embedding','embedding',{'data':[{'embedding':[1.,2.]}]}),
])
async def test_shared_client_uses_model_timeout_for_every_inference_protocol(protocol,kind,payload):
    def respond(request):
        assert request.extensions['timeout']==dict(connect=60,read=60,write=60,pool=60)
        return httpx.Response(200,json=payload)
    config=ModelServiceConfig(key='test',name='test',kind=kind,protocol=protocol,
        base_url='http://model.test',endpoint='/infer',model_name='test',timeout_seconds=60)
    image=BytesIO();Image.new('RGB',(16,16)).save(image,'PNG')
    async with httpx.AsyncClient(timeout=.02,transport=httpx.MockTransport(respond)) as client:
        result=await infer(config,image.getvalue(),16,16,client=client)
    assert result['kind']==kind


@pytest.mark.asyncio
@pytest.mark.parametrize('client_timeout,delay,times_out',[(.02,.1,False),(30,1.2,True)])
async def test_model_timeout_controls_real_shared_connection(client_timeout,delay,times_out):
    handlers=set()
    async def respond(reader,writer):
        handlers.add(asyncio.current_task())
        try:
            await reader.readuntil(b'\r\n\r\n')
            await asyncio.sleep(delay)
            body=b'{"detections":[]}'
            writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            await writer.drain()
        except ConnectionError:pass
        finally:
            writer.close()
            await writer.wait_closed()
    server=await asyncio.start_server(respond,'127.0.0.1',0)
    config=ModelServiceConfig(key='test',name='test',kind='detection',protocol='model_service',
        base_url='http://127.0.0.1:'+str(server.sockets[0].getsockname()[1]),endpoint='/detect',timeout_seconds=1)
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            if times_out:
                with pytest.raises(HTTPException) as error:await infer(config,b'image',16,16,client=client)
                assert error.value.status_code==504
            else:
                assert (await infer(config,b'image',16,16,client=client))['boxes']==[]
    finally:
        server.close();await server.wait_closed()
        await asyncio.gather(*handlers)
