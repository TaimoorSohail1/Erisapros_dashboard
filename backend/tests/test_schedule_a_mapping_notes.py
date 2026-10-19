from app.services.schedule_a_pdf_source import mapping_note_character_keys, character_key, has_substantive_native_text


def chars(text, x=10, top=10, color=(.98,.74,.02)):
    return [dict(text=c,x0=x+i*5,x1=x+(i+1)*5,top=top,bottom=top+10,
                 size=10,fontname='Arial',non_stroking_color=color) for i,c in enumerate(text)]


def test_only_instruction_block_is_excluded_not_same_colored_source_value():
    instruction=chars('Field 1a')
    value=chars('Example Life Insurance Company',x=100,top=100)
    keys=mapping_note_character_keys(instruction+value)
    assert keys=={character_key(c) for c in instruction}


def test_clean_colored_document_without_mapping_instructions_is_unchanged():
    source=chars('Life insurance 13500.25')
    assert mapping_note_character_keys(source)==set()


def test_wrapped_instruction_is_one_block_and_black_source_is_preserved():
    first=chars('Fields 3c and 3d')
    continuation=chars('should be blank',top=22)
    source=chars('Commissions paid: 1500',top=22,color=(0,0,0))
    assert mapping_note_character_keys(first+continuation+source)=={character_key(c) for c in first+continuation}


def test_mapping_instruction_without_the_word_field_is_excluded():
    instruction = chars('Commissions into 3b if there is an amount')
    continuation = chars('Otherwise leave blank', top=22)
    printed = chars('Commissions 3b 12.50', top=100)
    assert mapping_note_character_keys(instruction + continuation + printed) == {
        character_key(c) for c in instruction + continuation
    }


def test_logo_and_notes_do_not_mask_an_image_only_insurance_statement():
    assert not has_substantive_native_text([(1, "Example healthcare\nPolicy periods to 1f and 1g")])
    assert has_substantive_native_text([(1, "EIN: 12-3456789")])
    assert has_substantive_native_text([(1, "Persons covered: 255")])


def test_instruction_without_field_number_is_not_carrier_data():
    note = chars('Dental should be checked on 5500 Sch A')
    assert mapping_note_character_keys(note) == {character_key(c) for c in note}
