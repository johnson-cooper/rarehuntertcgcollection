from django.utils.dateparse import parse_datetime, parse_date
from django.db import transaction
from .models import Card, CardSet, CollectionCard, ImportBatch, CollectionImage
import os
from django.conf import settings

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
    psa = payload.get('psa')
    q = CollectionCard.objects.filter(card=card)
    if card_set:
        q = q.filter(card_set=card_set)
    q = q.filter(edition__iexact=edition)
    if psa:
        q = q.filter(psa__iexact=psa)
    return q.first()

def _replace(obj, field, value):
    setattr(obj, field, value)

def _merge(obj, field, value):
    if value not in (None, '', []):
        setattr(obj, field, value)

def run_import_batch(import_batch: ImportBatch, json_data: dict):
    meta = json_data.get('meta') or {}
    cards = json_data.get('cards') or []

    # set exported_at on batch
    exported_at = meta.get('exported_at')
    if exported_at:
        try:
            import_batch.exported_at = parse_datetime(exported_at)
            import_batch.save()
        except:
            pass

    created = 0
    updated = 0
    deleted_count = 0
    new_ids = set()
    mode = import_batch.mode

    with transaction.atomic():
        # ---- REPLACE MODE: remove all old cards for this batch ----
        if mode == 'replace':
            old_cards = CollectionCard.objects.filter(import_batch=import_batch)
            for cc in old_cards:
                # delete associated images
                for img in cc.images.all():
                    img_path = os.path.join(settings.MEDIA_ROOT, str(img.img))
                    if os.path.exists(img_path):
                        os.remove(img_path)
                    img.delete()
                cc.delete()
            deleted_count = old_cards.count()

        # ---- IMPORT LOOP ----
        for payload in cards:
            card, card_set = _find_or_create_card_and_set(payload)

            existing_cc = None
            if mode != 'replace':
                existing_cc = _identify_collection_card(card, card_set, payload)

            edition = payload.get('edition') or 'Unlimited'
            condition = payload.get('condition')
            quantity = payload.get('quantity') or 1
            misprint = payload.get('misprint') or payload.get('misprints')
            psa = payload.get('psa')
            notes = payload.get('notes')

            pricing = payload.get('pricing') or {}
            low = pricing.get('low')
            mid = pricing.get('mid')
            high = pricing.get('high')
            effective_mid = pricing.get('effective_mid') or mid
            pricing_source = pricing.get('source')

            exported_id = payload.get('id')

            if existing_cc:
                # MERGE mode
                _merge(existing_cc, 'condition', condition)
                _merge(existing_cc, 'misprint', misprint)
                _merge(existing_cc, 'psa', psa)
                _merge(existing_cc, 'notes', notes)
                _merge(existing_cc, 'value_low', low)
                _merge(existing_cc, 'value_mid', mid)
                _merge(existing_cc, 'value_high', high)
                _merge(existing_cc, 'effective_mid', effective_mid)
                _merge(existing_cc, 'pricing_source', pricing_source)

                if existing_cc.quantity in (None, 0):
                    existing_cc.quantity = quantity
                if not existing_cc.exported_id and exported_id:
                    existing_cc.exported_id = exported_id

                existing_cc.import_batch = import_batch
                existing_cc.save()
                updated += 1
                if exported_id:
                    new_ids.add(int(exported_id))
            else:
                # CREATE new row
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

    return created, updated, deleted_count
