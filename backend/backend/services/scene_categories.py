"""Two-level scene metadata; stored leaf labels remain compatible."""
from sqlalchemy import select,func,false
from schemas.image_ingest import ImageIngestConfig


def scene_result_status(status,labels):
    return 'unmatched' if status=='completed' and not labels else status


def has_scene_labels(column,dialect):
    if dialect=='sqlite':length=func.json_array_length(column)
    else:
        from sqlalchemy.dialects.postgresql import JSONB
        length=func.jsonb_array_length(column.cast(JSONB))
    return func.coalesce(length,0)>0


def scene_filter_values(scene,configs):
    if not scene.startswith('@group:'):return [scene]
    name=scene[len('@group:'):]
    values=[]
    for config in configs:
        if not isinstance(config,ImageIngestConfig):config=ImageIngestConfig.model_validate(config)
        for group in config.scene_groups:
            if group.name==name:
                if name not in values:values.append(name)
                values.extend(value for value in group.scenes if value not in values)
    return values


def scene_labels_condition(column,values,dialect):
    if not values:return false()
    if dialect=='sqlite':
        labels=func.json_each(column).table_valued('value')
        return select(1).select_from(labels).where(labels.c.value.in_(values)).exists()
    from sqlalchemy.dialects.postgresql import JSONB,array
    return column.cast(JSONB).op('?|')(array(values))
