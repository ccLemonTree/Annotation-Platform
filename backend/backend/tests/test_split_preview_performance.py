"""Bound preview decoding concurrency and retain exact pHash matching semantics."""
import hashlib
import threading
from io import BytesIO
from types import SimpleNamespace
import imagehash
import pytest
from PIL import Image
from fastapi import HTTPException
from services import detection_export, dataset_splits


def test_pairs_use_integer_hamming_distance_without_numpy_per_pair(monkeypatch):
    fingerprints={str(i):(imagehash.hex_to_hash(f'{i:016x}'),(i,10,20)) for i in range(32)}
    policy=dataset_splits.SplitPolicy(phash_distance=2,color_distance=5)
    items=sorted(fingerprints.items())
    expected=[(left,right) for index,(left,(h,c)) in enumerate(items)
              for right,(other,oc) in items[:index]
              if bin(int(str(h),16)^int(str(other),16)).count('1')<=2 and max(abs(a-b) for a,b in zip(c,oc))<=5]
    def no_array_subtraction(*args):raise AssertionError('per-pair NumPy subtraction')
    monkeypatch.setattr(imagehash.ImageHash,'__sub__',no_array_subtraction)
    assert list(dataset_splits.near_duplicate_pairs(fingerprints,policy))==expected


@pytest.mark.asyncio
async def test_preview_decodes_in_at_most_four_workers(monkeypatch):
    output=BytesIO();Image.new('RGB',(8,8),'red').save(output,format='PNG');data=output.getvalue()
    rows=[(SimpleNamespace(id=str(i),content_hash=hashlib.sha256(data).hexdigest(),blob_path=str(i),
                           width=8,height=8,device_info={}),SimpleNamespace(annotation_data={'boxes':[]})) for i in range(8)]
    async def export_rows(*args,**kwargs):return rows,[]
    monkeypatch.setattr(detection_export,'export_rows',export_rows)
    barrier=threading.Barrier(4,timeout=3);lock=threading.Lock();active=0;maximum=0
    class File(BytesIO):
        def close(self):
            nonlocal active
            if not self.closed:
                with lock:active-=1
            super().close()
    async def get_file(path):
        nonlocal active,maximum
        with lock:active+=1;maximum=max(maximum,active)
        barrier.wait()
        return File(data)
    monkeypatch.setattr(detection_export,'get_storage',lambda:SimpleNamespace(get_file=get_file))
    result=await detection_export.preview_splits(None,SimpleNamespace(label_schema={'labels':['fire']}),
        ['fire'],True,dataset_splits.SplitPolicy(),suggest=True)
    assert maximum==4 and active==0
    assert len(result['fingerprints'])==8 and result['near_duplicate_checked']


@pytest.mark.asyncio
async def test_preview_still_rejects_changed_file(monkeypatch):
    row=(SimpleNamespace(id='a',content_hash='wrong',blob_path='a',width=8,height=8),
         SimpleNamespace(annotation_data={'boxes':[]}))
    async def export_rows(*args,**kwargs):return [row],[]
    async def get_file(path):return BytesIO(b'changed file')
    monkeypatch.setattr(detection_export,'export_rows',export_rows)
    monkeypatch.setattr(detection_export,'get_storage',lambda:SimpleNamespace(get_file=get_file))
    with pytest.raises(HTTPException) as error:
        await detection_export.preview_splits(None,SimpleNamespace(label_schema={'labels':['fire']}),
            ['fire'],True,dataset_splits.SplitPolicy(),suggest=True)
    assert error.value.status_code==400 and '哈希' in error.value.detail
