from django.utils.dateparse import parse_datetime, parse_date
from .models import Card, CardSet, CollectionCard, ImportBatch


# -----------------------
# Helpers
# -----------------------

def _normalize(s):
    return (s or '').strip()


def _is_empty(val):
    return val is None or val == ''


def _merge(existing, field, new_val):
    """Only set if existing is empty and new value exists"""
    if _is_empty(getattr(existing, field)) and new_val is not None:
        setattr(existing, field, new_val)


def _replace(existing, field, new_val):
    setattr(existing, field, new_val)


# -----------------------
# Card + Set resolution
# -----------------------

def _find_or_create_card_and_set(card_payload):
    set_data = card_payload.get('set') or {}
    set_code = _normalize(set_data.get('code'))
    set_name = _normalize(set_data.get('name'))

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
            except Exception:
                pass

        card_set = CardSet.objects.create(
            name=set_name,
            code=set_code or None,
            release_date=release_date
        )

    konami_id = card_payload.get('konami_id')
    card_name = _normalize(card_payload.get('name'))

    card = None
    if konami_id:
        card = Card.objects.filter(konami_id=konami_id).first()
    if not card:
        card = Card.objects.filter(name__iexact=card_name).first()
    if not card:
        card = Card.objects.create(name=card_name, konami_id=konami_id)

    return card, card_set


# -----------------------
# CollectionCard matching
# -----------------------

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
            psa = psa_data.get('company') or psa_data.get('cert')
        else:
            psa = str(psa_data)

    q = CollectionCard.objects.filter(card=card)

    if card_set:
        q = q.filter(card_set=card_set)

    q = q.filter(edition__iexact=edition)

    if psa:
        q = q.filter(psa__iexact=psa)

    return q.first()


# -----------------------
# Main import runner
# -----------------------

def run_import_batch(import_batch: ImportBatch, json_data: dict):
    meta = json_data.get('meta') or {}
    cards = json_data.get('cards') or []

    if import_batch.mode == 'replace':
        CollectionCard.objects.all().delete()

    exported_at = meta.get('exported_at')
    if exported_at:
        try:
            import_batch.exported_at = parse_datetime(exported_at)
            import_batch.save()
        except Exception:
            pass

    created = 0
    updated = 0
    new_ids = set()

    mode = import_batch.mode  # 'merge' or 'replace'

    for payload in cards:
        card, card_set = _find_or_create_card_and_set(payload)
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
            if mode == 'replace':
                _replace(existing_cc, 'edition', edition)
                _replace(existing_cc, 'condition', condition)
                _replace(existing_cc, 'quantity', quantity)
                _replace(existing_cc, 'misprint', misprint)
                _replace(existing_cc, 'psa', psa)
                _replace(existing_cc, 'notes', notes)
                _replace(existing_cc, 'value_low', low)
                _replace(existing_cc, 'value_mid', mid)
                _replace(existing_cc, 'value_high', high)
                _replace(existing_cc, 'effective_mid', effective_mid)
                _replace(existing_cc, 'pricing_source', pricing_source)
                _replace(existing_cc, 'card_set', card_set)
                _replace(existing_cc, 'exported_id', exported_id)

            else:  # MERGE
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
            CollectionCard.objects.create(
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

    # -----------------------
    # Replace-mode deletions
    # -----------------------

    deleted_count = 0

    if mode == 'replace':
        previous_batches = ImportBatch.objects.filter(
            name__iexact=import_batch.name
        ).exclude(pk=import_batch.pk)

        prev_cards = CollectionCard.objects.filter(
            import_batch__in=previous_batches
        )

        new_keyset = set()

        for c in cards:
            if c.get('id'):
                new_keyset.add(('exported_id', int(c.get('id'))))
            else:
                name = (c.get('name') or '').strip().lower()
                set_code = ((c.get('set') or {}).get('code') or '').strip().lower()
                edition = (c.get('edition') or 'Unlimited').strip().lower()
                psa = (c.get('psa') or '').strip().lower()
                new_keyset.add(('fallback', (name, set_code, edition, psa)))

        for pc in prev_cards:
            should_delete = False

            if pc.exported_id:
                if int(pc.exported_id) not in new_ids:
                    should_delete = True
            else:
                fallback = (
                    (pc.card.name or '').strip().lower(),
                    (pc.card_set.code or '').strip().lower() if pc.card_set else '',
                    (pc.edition or 'Unlimited').strip().lower(),
                    (pc.psa or '').strip().lower()
                )
                if ('fallback', fallback) not in new_keyset:
                    should_delete = True

            if should_delete:
                pc.delete()
                deleted_count += 1

    return created, updated, deleted_count
