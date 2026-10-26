"""019 migration checked only in fixture-owned PostgreSQL databases."""
import json
from alembic import command
from sqlalchemy import text
from tests.test_database_closeout_postgres import isolated_pg, pre_013


def test_019_only_adds_prompt_column_and_keeps_existing_models(isolated_pg):
    engine, config = isolated_pg
    pre_013(engine, config); command.upgrade(config, '018')
    with engine.begin() as db:
        db.execute(text("""INSERT INTO model_services
            (id,tenant_id,key,name,kind,protocol,base_url,endpoint,model_name,enabled,timeout_seconds,confidence,iou,labels,label_mapping,api_key_env,revision)
            VALUES ('11111111-1111-4111-8111-111111111111',1,'qwen','Qwen','caption','openai_chat',
            'http://model.test/v1','/chat/completions','Qwen3.8-27B',true,60,.25,.45,'[]','{}','',7)"""))
        before=db.scalar(text('SELECT row_to_json(t)::text FROM model_services t'))
    command.upgrade(config, '019')
    with engine.connect() as db:
        assert db.scalar(text("SELECT (to_jsonb(t)-'chat_prompts') FROM model_services t")) == json.loads(before)
        assert db.scalar(text('SELECT chat_prompts FROM model_services')) == {}
        assert db.scalar(text('SELECT revision FROM model_services')) == 7
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '019'
