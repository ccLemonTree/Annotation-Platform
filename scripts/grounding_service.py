"""Single-GPU grounding adapter for the platform's multipart detection protocol."""
from contextlib import asynccontextmanager
from io import BytesIO
import json
import logging
import os
import re
import threading

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

log = logging.getLogger('uvicorn.error')


def categories_from_request(labels, prompt):
    if prompt.strip():
        categories = [part.strip() for part in re.split('[;；\n]', prompt) if part.strip()]
    else:
        value = json.loads(labels)
        if not isinstance(value, (dict, list)):
            raise ValueError('labels must be a JSON object or string list')
        categories = list(value) or ['electric bicycle']
    if not 1 <= len(categories) <= 100 or any(
        not isinstance(name, str) or not name.strip() or len(name) > 2000 for name in categories
    ):
        raise ValueError('provide 1..100 nonempty category names')
    return categories


def groundingpi_boxes(predictions, width, height):
    rows = []
    for prediction in predictions:
        for box in prediction['coordinates'] or ():
            rows.append({'class': prediction['label'],
                         'bbox': [v * size / 999 for v, size in zip(box, (width,height,width,height))],
                         'score': .5})
    return rows


class VLXEngine:
    def __init__(self):
        from vlx_seek_worker import VLXSeekWorker
        self.worker = VLXSeekWorker(os.environ['MODEL_PATH'], device='cuda:0')

    def detect(self, image, categories):
        from inference import load_wedetect_proposals
        log.info('VLX proposal generation started')
        proposals = load_wedetect_proposals(image, '/resources/wedetect_base_uni.pth')
        log.info('VLX proposal generation completed: %d boxes', len(proposals))
        result = self.worker.detect(image, proposals, categories, max_new_tokens=1024, temperature=0)
        log.info('VLX generation completed: %d tokens', result['completion_tokens'])
        if result['completion_tokens'] >= 1024:
            raise ValueError('VLX-Seek generation reached its token limit; partial boxes are rejected')
        return {'detections': [dict(
            **{'class': box['label']}, bbox=[box[k] for k in ('xmin','ymin','xmax','ymax')], score=.5
        ) for box in result['result_bbox_list']], 'raw_output': result['answer']}


class GroundingPIEngine:
    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        self.torch = torch
        path = os.environ['MODEL_PATH']
        self.processor = AutoProcessor.from_pretrained(path, trust_remote_code=True, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(path, trust_remote_code=True,
            local_files_only=True, dtype=torch.bfloat16, attn_implementation='sdpa').eval().to('cuda:0')

    def detect(self, image, categories):
        from grounding_pi.client import parse_response
        from grounding_pi.protocol import build_detection_prompt
        from infer.serve_vlm import expand_image_tokens, render_prompt
        messages = [{'role':'user','content':[{'type':'image','image':'provided'},
                    {'type':'text','text':build_detection_prompt(categories)}]}]
        prompt = render_prompt(self.processor, messages, True)
        image_inputs = self.processor.image_processor(images=[image], return_tensors='pt')
        merge = self.processor.image_processor.merge_size ** 2
        prompt = expand_image_tokens(prompt, [int(grid.prod())//merge for grid in image_inputs['image_grid_thw']])
        inputs = dict(self.processor.tokenizer([prompt], return_tensors='pt'), **image_inputs)
        length = inputs['input_ids'].shape[1]
        if length + 1024 > 16384:
            raise ValueError('Image and prompt exceed model context limit')
        inputs = {key:value.to('cuda:0') for key,value in inputs.items()}
        with self.torch.inference_mode():
            tokens = self.model.generate(**inputs, max_new_tokens=1024, do_sample=False)[0,length:].tolist()
        eos = self.model.generation_config.eos_token_id
        eos = {eos} if isinstance(eos, int) else set(eos or [])
        finished = bool(tokens and tokens[-1] in eos) or len(tokens) < 1024
        while tokens and tokens[-1] in eos:
            tokens.pop()
        raw = self.processor.tokenizer.decode(tokens, skip_special_tokens=False, spaces_between_special_tokens=False)
        response = {'choices':[{'message':{'content':raw}, 'finish_reason':'stop' if finished else 'length'}]}
        parsed = parse_response(response, 'bbox')
        if not parsed.valid:
            raise ValueError('GroundingPI returned incomplete or malformed spatial tokens: ' + str(parsed.parse_error))
        return {'detections':groundingpi_boxes(parsed.to_dict()['predictions'], *image.size),
                'raw_output':parsed.raw_output}


def create_app(engine=None):
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = engine or (
            VLXEngine() if os.environ['MODEL_KIND'] == 'vlx' else GroundingPIEngine())
        yield

    app = FastAPI(title='Platform Grounding Service', lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'status':'ok', 'model':os.environ.get('MODEL_KIND', 'test')}

    @app.post('/detect/grounding')
    def detect(image: UploadFile = File(...), labels: str = Form('{}', max_length=20000),
               prompt: str = Form('', max_length=2000), conf: float = Form(.25, ge=0, le=1),
               nms: float = Form(.45, ge=0, le=1)):
        try:
            categories = categories_from_request(labels, prompt)
            content = image.file.read(16*1024*1024 + 1)
            if len(content) > 16*1024*1024:
                raise HTTPException(413, 'Image exceeds 16 MiB')
            with Image.open(BytesIO(content)) as original:
                width, height = original.size
                if width*height > 16_000_000:
                    raise HTTPException(413, 'Image exceeds 16 megapixels')
                picture = original.convert('RGB')
        except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(400, 'Invalid image or category list')
        picture.thumbnail((1536,1536))
        try:
            with lock:
                result = app.state.engine.detect(picture, categories)
            for row in result['detections']:
                row['bbox'] = [v*scale for v,scale in zip(row['bbox'],
                    (width/picture.width,height/picture.height)*2)]
            # shortcut: 0.5 is a protocol placeholder, replace only after task-specific confidence calibration.
            return {**result, 'score_type':'uncalibrated',
                    'warnings':['VLM scores are uncalibrated placeholders; review all predictions.'],
                    'width':width, 'height':height}
        except Exception:
            logging.exception('Grounding inference failed')
            raise HTTPException(502, 'Grounding inference failed; inspect service logs')

    return app


app = create_app()
