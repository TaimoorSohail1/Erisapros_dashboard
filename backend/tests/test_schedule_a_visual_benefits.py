from PIL import Image, ImageDraw
import pytest
from app.services.schedule_a_visual_benefits import checked_benefit_boxes


@pytest.mark.parametrize('label,kind', [
    ('Temporary','temporary_disability'), ('Long-term','long_term_disability'),
    ('Supplemental','supplemental_unemployment'), ('Prescription','prescription_drug'),
    ('Stop','stop_loss'), ('Other','other'),
])
def test_changed_benefit_selections_are_read_by_label_not_by_sample_selection(label, kind):
    image = Image.new('L',(450,200),255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((80,80,99,99),outline=0,width=2)
    draw.line((84,89,89,95,96,84),fill=0,width=2)
    values = checked_benefit_boxes(image,[{'text':label,'left':110,'top':82,'width':90,'height':17}])
    assert len(values) == 1
    assert values[0]['benefit_type'] == kind and values[0]['selected']


def test_visible_checked_box_is_selected_without_selecting_the_neighboring_empty_box():
    image = Image.new('L', (450, 200), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 99, 99), outline=0, width=2)
    draw.line((84, 89, 89, 95, 96, 84), fill=0, width=2)
    draw.rectangle((230, 80, 249, 99), outline=0, width=2)
    words = [
        {'text':'Dental', 'left':110, 'top':82, 'width':60, 'height':17},
        {'text':'Vision', 'left':260, 'top':82, 'width':60, 'height':17},
    ]
    values = checked_benefit_boxes(image, words)
    assert [(value['benefit_type'], value['selected']) for value in values] == [('dental', True), ('vision', False)]
    assert values[0]['bounding_box'] == [80,80,100,100]


def test_similar_text_and_ambiguous_multiple_boxes_do_not_establish_a_selection():
    image = Image.new('L', (450, 200), 255)
    draw = ImageDraw.Draw(image)
    draw.ellipse((80,80,99,99), outline=0, width=2)
    word = {'text':'Dental', 'left':110, 'top':82, 'width':60, 'height':17}
    assert checked_benefit_boxes(image, [word]) == []
    draw.rectangle((45,80,64,99), outline=0, width=2)
    draw.rectangle((80,80,99,99), outline=0, width=2)
    assert checked_benefit_boxes(image, [word]) == []


def test_words_outside_the_benefit_section_do_not_become_selected_benefits():
    from app.services.schedule_a_visual_benefits import benefit_evidence_from_ocr
    image = Image.new('L',(450,200),255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((80,80,99,99),outline=0,width=2)
    draw.line((84,89,89,95,96,84),fill=0,width=2)
    words = [{'text':'Dental','left':110,'top':82,'width':60,'height':17,'line_num':'2'}]
    assert benefit_evidence_from_ocr(image,words,3)['observations'] == []
    words.append({'text':'Welfare Benefit Contract Information','left':20,'top':20,'width':200,'height':15,'line_num':'1'})
    result = benefit_evidence_from_ocr(image,words,3)
    assert result['observations'][0]['benefit_type'] == 'dental'
    assert result['observations'][0]['page'] == 3


def test_visual_evidence_supplements_source_review_without_overwriting_any_extracted_field():
    from app.models import NormalizedExtractionField, NormalizedExtractionResult
    from app.services.schedule_a_source_observations import attach_schedule_a_source_observations
    from app.services.schedule_a_visual_benefits import attach_visual_benefit_evidence
    result = NormalizedExtractionResult(provider='Example', fields=[
        NormalizedExtractionField(field_name='10a. Total premiums or subscription charges paid to carrier',value='1250.50')])
    attach_schedule_a_source_observations(result, [(2,'Welfare Benefit Contract Information')])
    before = [field.model_dump() for field in result.fields]
    attach_visual_benefit_evidence(result, {'status':'COMPLETED','pages':[{
        'page':2, 'checkboxes':[], 'observations':[{
            'benefit_type':'dental','literal':'Dental','page':2,'decision':'SOURCE_ONLY',
            'source_text':'Visible marked square immediately before Dental',
        }],
    }]})
    assert result.raw['source_review']['benefit_types'] == ['dental']
    assert result.raw['benefit_types'] == ['dental']
    assert [field.model_dump() for field in result.fields] == before
