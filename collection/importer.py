from django.utils.dateparse import parse_datetime, parse_date
from .models import Card, CardSet, CollectionCard, ImportBatch

def _normalize(s):
    return (s or '').strip()

def _find_or_create_card_and_set(card_payload):
    set_data = card_payload.get('set') or {}
    set_code = _normalize(set_data.get('code') or '')
    set_name = _normalize(set_data.get('name') or '')

    card_set = None
    if set_code:
        card_set = CardSet.objects.filter(code__iexact=set_code).first()
    if not card_set and set_name:
        card_set = CardSet.objects.filter(name__iexact=set_name).first()
    if not card_set:
        release_date = None
        rd = set_data.get('release_date')
        if rd:
            try:
                release_date = parse_date(rd)
            except:
                release_date = None
        card_set = CardSet.objects.create(name=set_name, code=set_code or None, release_date=release_date)

    konami_id = card_payload.get('konami_id')
    card_name = _normalize(card_payload.get('name') or '')

    card = None
    if konami_id:
        card = Card.objects.filter(konami_id=konami_id).first()
    if not card:
        card = Card.objects.filter(name__iexact=card_name).first()
    if not card:
        card = Card.objects.create(name=card_name, konami_id=konami_id)

    return card, card_set

def _identify_collection_card(card, card_set, payload):
    exported_id = payload.get('id')
    if exported_id:
        cc = CollectionCard.objects.filter(exported_id=exported_id).first()
        if cc:
            return cc
    edition = payload.get('edition') or 'Unlimited'
    psa = None
    psa_data = payload.get('psa')
    if psa_data:
        if isinstance(psa_data, dict):
            psa = psa_data.get('company') or psa_data.get('cert') or str(psa_data)
        else:
            psa = str(psa_data)
    q = CollectionCard.objects.filter(card=card)
    if card_set:
        q = q.filter(card_set=card_set)
    q = q.filter(edition__iexact=edition)
    if psa:
        q = q.filter(psa__iexact=psa)
    return q.first()

def run_import_batch(import_batch: ImportBatch, json_data: dict):
    meta = json_data.get('meta') or {}
    cards = json_data.get('cards') or []

    exported_at = meta.get('exported_at')
    if exported_at:
        try:
            import_batch.exported_at = parse_datetime(exported_at)
            import_batch.save()
        except:
            pass

    created = 0
    updated = 0
    new_ids = set()

    for c in cards:
        card, card_set = _find_or_create_card_and_set(c)
        existing_cc = _identify_collection_card(card, card_set, c)
        edition = c.get('edition') or 'Unlimited'
        condition = c.get('condition') or ''
        quantity = c.get('quantity') or 1
        misprint = c.get('misprint') or c.get('misprints') or None
        psa = c.get('psa') or None
        notes = c.get('notes') or None
        pricing = c.get('pricing') or {}
        low = pricing.get('low')
        mid = pricing.get('mid')
        high = pricing.get('high')
        effective_mid = pricing.get('effective_mid') or mid
        pricing_source = pricing.get('source')
        exported_id = c.get('id')
        if existing_cc:
            existing_cc.edition = edition
            existing_cc.condition = condition
            existing_cc.quantity = quantity
            existing_cc.misprint = misprint
            existing_cc.psa = psa
            existing_cc.notes = notes
            existing_cc.value_low = low if low is not None else existing_cc.value_low
            existing_cc.value_mid = mid if mid is not None else existing_cc.value_mid
            existing_cc.value_high = high if high is not None else existing_cc.value_high
            existing_cc.effective_mid = effective_mid if effective_mid is not None else existing_cc.effective_mid
            existing_cc.pricing_source = pricing_source or existing_cc.pricing_source
            existing_cc.import_batch = import_batch
            existing_cc.exported_id = exported_id or existing_cc.exported_id
            existing_cc.save()
            updated += 1
            if exported_id:
                new_ids.add(int(exported_id))
        else:
            cc = CollectionCard.objects.create(
                card=card,
                card_set=card_set,
                edition=edition,
                condition=condition,
                quantity=quantity,
                misprint=misprint,
                psa=psa,
                notes=notes,
                value_low=low,
                value_mid=mid,
                value_high=high,
                effective_mid=effective_mid,
                pricing_source=pricing_source,
                import_batch=import_batch,
                exported_id=exported_id
            )
            created += 1
            if exported_id:
                new_ids.add(int(exported_id))

    deleted_count = 0
    if import_batch.mode == 'replace':
        previous_batches = ImportBatch.objects.filter(name__iexact=import_batch.name).exclude(pk=import_batch.pk)
        prev_cards = CollectionCard.objects.filter(import_batch__in=previous_batches)
        new_keyset = set()
        for c in cards:
            if c.get('id'):
                new_keyset.add(('exported_id', int(c.get('id'))))
            else:
                name = (c.get('name') or '').strip().lower()
                set_code = (c.get('set') or {}).get('code') or ''
                edition = (c.get('edition') or 'Unlimited').strip().lower()
                psa = (c.get('psa') or '').strip().lower() if c.get('psa') else ''
                new_keyset.add(('fallback', (name, set_code.strip().lower(), edition, psa)))
        for pc in prev_cards:
            should_delete = False
            if pc.exported_id and int(pc.exported_id) not in new_ids:
                should_delete = True
            else:
                fallback_key = ('fallback', ( (pc.card.name or '').strip().lower(), (pc.card_set.code or '').strip().lower() if pc.card_set and pc.card_set.code else '', (pc.edition or 'Unlimited').strip().lower(), (pc.psa or '').strip().lower() ))
                if fallback_key not in new_keyset and not (pc.exported_id and int(pc.exported_id) in new_ids):
                    should_delete = True
            if should_delete:
                pc.delete()
                deleted_count += 1

    return created, updated, deleted_count
