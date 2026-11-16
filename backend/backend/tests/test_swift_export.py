"""Training samples follow independent image/message alignment and human judgments."""
import pytest
from fastapi import HTTPException


def convert(documents=(),pairs=(),format='infonce',source='descriptions',instruction='请描述图片。'):
    from services.swift_export import dataset
    return dataset(documents,pairs,format,source,instruction)


def test_single_attribute_pairs_skip_same_value_and_reuse_image_identity(monkeypatch):
    from types import SimpleNamespace
    from services import embedding_negative_recommendations as recommendations
    from services.single_attribute_export import attribute_pairs

    identities = []

    def original_id(asset):
        identities.append(asset.id)
        return asset.origin

    monkeypatch.setattr(recommendations, 'original_id', original_id)
    rows = [
        (SimpleNamespace(id=image_id, origin=origin, content_hash=image_id),
         SimpleNamespace(annotation_data={
             'attribute_schema': {'target_label': ['car'], 'fields': [{'key': 'body_color', 'name': '车身颜色'}]},
             'attributes': {'body_color': {'value': value}},
         }))
        for image_id, origin, value in [('1', 'shared', '白色'), ('2', '2', '白色'), ('3', '3', '白色'),
                                         ('4', 'shared', '黑色'), ('5', '5', '黑色')]
    ]
    pairs = list(attribute_pairs(rows, 3, 'both'))
    assert [(anchor, negatives) for text, anchor, negatives in pairs if '为白色' in text] == [
        ('1', ['5']), ('2', ['4', '5']), ('3', ['4', '5']),
    ]
    assert [(anchor, negatives) for text, anchor, negatives in pairs if '不是白色' in text] == [
        ('4', ['2', '3']), ('5', ['1', '2', '3']),
    ]
    assert sorted(identities) == ['1', '2', '3', '4', '5']


@pytest.mark.parametrize('format',['infonce','contrastive','online_contrastive','cosine_similarity'])
def test_description_image_pairs_require_no_vectors(format):
    rows,report=convert([{'image':'images/人.png','description':'穿黑衣服的人','labels':[]}],format=format)
    assert rows[0]['messages']==[{'role':'user','content':'穿黑衣服的人'}]
    assert rows[0]['positive_messages']==[[{'role':'user','content':'<image>'}]]
    assert rows[0]['positive_images']==[['images/人.png']]
    assert 'images' not in rows[0] and 'negative_messages' not in rows[0]
    assert ('label' not in rows[0]) if format=='infonce' else rows[0]['label']==1
    assert report['samples']==1


def test_sft_uses_assistant_caption_and_one_image_with_editable_instruction():
    rows,_=convert([{'image':'images/a.png','description':'戴着口罩','labels':[]}],format='swift_sft',instruction='请看<image>，描述口罩。')
    assert rows==[{'messages':[{'role':'user','content':'请看<image>，描述口罩。'},{'role':'assistant','content':'戴着口罩'}],'images':['images/a.png']}]


def test_attribute_descriptions_use_known_values_as_natural_language():
    document={'image':'images/a.png','attribute_schema':{'target_label':['car','truck'],'fields':[
        {'key':'body_color','name':'车身颜色'},{'key':'vehicle_type','name':'车辆类型'},{'key':'brand','name':'车辆品牌'}]},
        'attributes':{'body_color':{'value':'白色'},'vehicle_type':{'value':'轿车'},'brand':{'value':None}}}
    expected='图中这辆车的车身颜色为白色，车辆类型为轿车。'
    sft,_=convert([document],format='swift_sft',source='attribute_descriptions')
    pairs,_=convert([document],source='attribute_descriptions')
    assert sft[0]['messages'][-1]['content']==pairs[0]['messages'][0]['content']==expected
    fallback,_=convert([document],source='descriptions')
    assert fallback[0]['messages'][0]['content']==expected


def test_fixed_person_caption_exports_as_attributes_and_natural_language():
    import json

    document = {'image': 'images/person.png', 'description': json.dumps({
        '体型': ['标准'], '发色': '无法识别', '穿着': '黑色外套', '配饰': ['眼镜', '背包'],
    }, ensure_ascii=False)}
    sft, _ = convert([document], format='swift_sft', source='attributes')
    assert json.loads(sft[0]['messages'][-1]['content']) == {
        'body_type': ['标准'], 'clothing': '黑色外套', 'accessories': ['眼镜', '背包'],
    }
    pairs, _ = convert([document], source='attribute_descriptions')
    assert pairs[0]['messages'][0]['content'] == '图中这名人员的体型为标准，穿着为黑色外套，配饰为眼镜、背包。'


def test_labels_are_explicit_pairs_and_never_invent_negatives():
    rows,report=convert([{'image':'images/a.png','description':'另一段描述','labels':['黑衣','口罩']}],source='labels')
    assert [row['messages'][0]['content'] for row in rows]==['黑衣','口罩']
    assert all('negative_messages' not in row for row in rows)
    assert report['samples']==2


def test_infonce_splits_positives_keeps_only_judged_negatives_and_image_anchor():
    pair={'query':{'image':'queries/a.jpg'},'positives':[{'image':'images/p1.jpg'},{'image':'images/p2.jpg'}],
        'negatives':[{'image':'images/n.jpg'}],'unknown':['unknown-id']}
    rows,_=convert(pairs=[pair],source='matches')
    assert len(rows)==2
    for i,row in enumerate(rows):
        assert row['images']==['queries/a.jpg'] and row['messages']==[{'role':'user','content':'<image>'}]
        assert row['positive_images']==[[f'images/p{i+1}.jpg']]
        assert row['negative_images']==[['images/n.jpg']]
        assert row['negative_messages']==[[{'role':'user','content':'<image>'}]]
        assert 'label' not in row


@pytest.mark.parametrize('format',['contrastive','online_contrastive','cosine_similarity'])
def test_pair_losses_use_binary_human_labels_not_retrieval_scores(format):
    pair={'query':{'text':'白色轿车'},'positives':[{'image':'images/p.jpg','score':.72}],
        'negatives':[{'image':'images/n.jpg','score':.98}],'unknown':['unknown-id']}
    rows,_=convert(pairs=[pair],format=format,source='matches')
    assert [row['label'] for row in rows]==[1,0]
    assert [row['positive_images'] for row in rows]==[[['images/p.jpg']],[['images/n.jpg']]]


def test_no_positive_infonce_is_not_silently_exported_as_valid_data():
    pair={'query':{'text':'人'},'positives':[],'negatives':[{'image':'n.png'}]}
    with pytest.raises(HTTPException) as error:convert(pairs=[pair],source='matches')
    assert error.value.status_code==400
    rows,_=convert(pairs=[pair],source='matches',format='contrastive')
    assert rows[0]['label']==0


@pytest.mark.parametrize('text',['<image>多余占位','有<video>','有<audio>'])
def test_image_only_export_rejects_unbound_modality_tokens(text):
    with pytest.raises(HTTPException) as error:convert([{'image':'a.png','description':text}])
    assert error.value.status_code==400


def test_empty_descriptions_are_skipped_instead_of_using_labels_implicitly():
    rows,report=convert([{'image':'a.png','description':'','labels':['黑衣']},{'image':'b.png','description':'口罩'}])
    assert len(rows)==1 and rows[0]['positive_images']==[['b.png']]
    assert report['skipped_empty']==1
