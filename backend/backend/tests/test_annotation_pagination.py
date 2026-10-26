"""Bulk imports share timestamps; every annotation must appear once across pages."""
from datetime import datetime
from uuid import UUID
import pytest
from models import Asset,Annotation


@pytest.mark.asyncio
async def test_equal_timestamp_versions_have_stable_unique_pagination(client,auth_headers,test_project,test_db):
    ids=[UUID(f'abcdefab-0000-4000-8000-{i:012x}') for i in (32,16,48,8,40)]
    for i,id in enumerate(ids):
        asset=Asset(project_id=test_project.id,original_name=f'{i}.jpg',blob_path=f'{i}.jpg',
                    content_hash=f'pagination-{i}',file_size=1,source_type='upload')
        test_db.add(asset);await test_db.flush()
        test_db.add(Annotation(id=id,asset_id=asset.id,version=1,is_latest=True,
                              annotation_data={'boxes':[]},review_status='approved',created_at=datetime(2026,10,20)))
    await test_db.commit()
    found=[]
    for page in range(1,4):
        response=client.get('/api/annotations',headers=auth_headers,
            params={'project_id':str(test_project.id),'page':page,'page_size':2,'latest_only':True})
        assert response.status_code==200,response.text
        data=response.json();assert data['total']==5
        found.extend(UUID(item['id']) for item in data['items'])
    assert found==sorted(ids)
    assert len(found)==len(set(found))==5
