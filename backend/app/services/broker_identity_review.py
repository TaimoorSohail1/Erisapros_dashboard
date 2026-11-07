"""Explicit recipient grouping, kept separate from whole-list/send permission."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re

from app.models import ExtractionValidationResult
from app.services.schedule_a_broker_roster import _money
from app.services.schedule_a_broker_matching import BROKER_IDENTITY_ATTRIBUTES, broker_identity_review_key
from app.services.schedule_a_customer_rules import canonical_broker_payment_purpose


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def _rows_digest(rows):
    return _digest([r.model_dump(mode='json') for r in rows])


def _identity_error(row):
    return any(v.validator=='recipient_identity_ambiguity' and v.status=='ERROR' for v in row.validation_results)


def identity_groups(rows,status,source_context,snapshot):
    grouped={}
    for index,row in enumerate(rows):
        name=re.sub(r'[^A-Z0-9]','',row.name.upper())
        if name and _identity_error(row):grouped.setdefault(name,[]).append(index)
    result=[]
    for indexes in grouped.values():
        if len(indexes)<2:continue  # A missing counterpart is not permission to invent one.
        originals=[rows[i] for i in indexes]
        group_id=_digest({'source':status.source_sha256,'indexes':indexes,'rows':_rows_digest(originals)})
        proof=next((p for p in reversed(status.identity_reviews) if p.get('group_id')==group_id
            and p.get('choice') in {'SAME','SEPARATE'}
            and (p.get('address_source_index') in indexes if p.get('choice')=='SAME' else p.get('address_source_index') is None)
            and p.get('actor') and p.get('reason') and p.get('verified') is True
            and p.get('source_context_sha256')==source_context and p.get('snapshot_sha256')==snapshot
            and p.get('source_sha256')==status.source_sha256 and p.get('rows_sha256')==_rows_digest(rows)),None)
        def total(key):
            values=[_money(getattr(row,key)) for row in originals]
            return format(sum((Decimal(v) for v in values),Decimal(0)),'f') if all(v is not None for v in values) else None
        result.append({'id':group_id,'name':originals[0].name,'source_indexes':indexes,
            'address_options':[{'source_index':i,'address':', '.join(str(getattr(rows[i],key) or '').strip()
                for key in ('address_line_1','address_line_2','city','state','zip_code') if getattr(rows[i],key))} for i in indexes],
            'commission_total':total('commission_total'),'fee_total':total('fee_total'),
            'choice':proof.get('choice') if proof else None,
            'address_source_index':proof.get('address_source_index') if proof else None,
            'confirmed_by':proof.get('actor') if proof else None,'confirmed_at':proof.get('at') if proof else None})
    return result


def save_identity_choice(rows,status,request,*,actor,source_context,snapshot):
    from app.services.broker_review_confirmation import review_token
    if request.expected_context!=review_token(rows,status,source_context,snapshot):
        raise ValueError('Broker source or FTWilliam data changed. Refresh before confirming identity.')
    if not actor or not request.verified or not request.reason.strip() or not source_context or not snapshot:
        raise ValueError('Verify the recipient identity and provide an authenticated review reason.')
    groups=identity_groups(rows,status,source_context,snapshot)
    group=next((g for g in groups if g['id']==request.group_id),None)
    if not group:raise ValueError('The source recipient group changed. Refresh and review it again.')
    if request.choice=='SAME' and request.address_source_index not in group['source_indexes']:
        raise ValueError('Choose which verified source address to use for the combined broker.')
    if request.choice=='SEPARATE' and request.address_source_index is not None:
        raise ValueError('Separate brokers retain each source address; do not select a combined address.')
    proof={'group_id':group['id'],'choice':request.choice,'source_indexes':group['source_indexes'],
        'address_source_index':request.address_source_index,'verified':True,'actor':actor,
        'at':datetime.now(timezone.utc).isoformat(),'reason':request.reason.strip(),
        'source_context_sha256':source_context,'snapshot_sha256':snapshot,'source_sha256':status.source_sha256,
        'rows_sha256':_rows_digest(rows),'original_rows':[rows[i].model_dump(mode='json') for i in group['source_indexes']]}
    saved=status.model_copy(deep=True)
    saved.identity_reviews=[p for p in saved.identity_reviews if p.get('group_id')!=group['id']]+[proof]
    saved.reviewer_confirmation=None
    saved.state,saved.mode='NEEDS_REVIEW','PRESERVE'
    saved.identity_groups=identity_groups(rows,saved,source_context,snapshot)
    return saved


def confirmed_identity_decisions(rows,original_rows,sources,status,source_context,snapshot):
    """Only a stored, current decision can resolve this one source warning."""
    by_index={int(source[7:]):row for source,row in zip(sources,rows)
              if source.startswith('SOURCE:') and source[7:].isdigit()}
    decisions=[]
    for group in identity_groups(original_rows,status,source_context,snapshot):
        if not group['choice'] or not any(i in by_index for i in group['source_indexes']):continue
        if group['choice']=='SAME' and not all(i in by_index for i in group['source_indexes']):
            raise ValueError('A combined broker requires every confirmed source entry. Include them or confirm separate entries.')
        for index in group['source_indexes']:
            if index not in by_index:continue
            before,after=original_rows[index],by_index[index]
            if (any(str(getattr(before,key) or '').strip()!=str(getattr(after,key) or '').strip()
                    for key in BROKER_IDENTITY_ATTRIBUTES)
                    or any(_money(getattr(before,key))!=_money(getattr(after,key)) for key in ('commission_total','fee_total'))):
                raise ValueError('Broker identity or payment values changed after identity confirmation. Discard the draft edits and review identity again.')
        decisions.append(group)
    return decisions


def apply_identity_decisions(verified,sources,decisions):
    by_index={int(source[7:]):row for source,row in zip(sources,verified)
              if source.startswith('SOURCE:') and source[7:].isdigit()}
    merged={};skip=set()
    def attest(row):
        row.validation_results.append(ExtractionValidationResult(validator='reviewer_broker_identity_confirmation',status='PASS',
            normalized_value=broker_identity_review_key(row),reason='Reviewer explicitly confirmed the source recipient grouping and selected address.'))
    for group in decisions:
        indexes=group['source_indexes']
        if group['choice']=='SEPARATE':
            for i in indexes:
                if i in by_index:attest(by_index[i])
            continue
        target=by_index[group['address_source_index']].model_copy(deep=True)
        members=[by_index[i] for i in indexes]
        for key in ('commission_total','fee_total'):
            setattr(target,key,format(sum((Decimal(_money(getattr(row,key))) for row in members),Decimal(0)),'f'))
        target.purpose=canonical_broker_payment_purpose(target.commission_total,target.fee_total)
        target.evidence=[deepcopy(e) for row in members for e in row.evidence]
        target.validation_results=[v.model_copy(deep=True) for row in members for v in row.validation_results]
        attest(target)
        merged[min(indexes)]=target;skip.update(set(indexes)-{min(indexes)})
    result=[];result_sources=[]
    for source,row in zip(sources,verified):
        index=int(source[7:]) if source.startswith('SOURCE:') and source[7:].isdigit() else None
        if index in skip:continue
        result.append(merged.get(index,row));result_sources.append(source)
    return result,result_sources
