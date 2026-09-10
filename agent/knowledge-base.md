# LumaField — public-lighting reconciliation domain

This document explains domain concepts. It contains no operational record for any pole and must never be used as the source for claims about a specific asset's current state. For a specific pole, a LumaField tool is the authoritative source.

## What is being reconciled

A field verification compares four sources without overwriting any of them:

1. **Utility record:** technology, nominal lamp power, auxiliary equipment, total registered load, and last-update date.
2. **Technician observation:** what the person reports seeing, preserved as a human statement.
3. **Visual evidence:** a general photo of the luminaire and, when available, a photo of its label, model, driver, or ballast.
4. **Operational context:** location, date and time, work orders, and known interventions.

The result is a reconciliation: utility record confirmed, registry discrepancy, insufficient evidence, or manual review. Visual classification is only one piece of evidence subordinate to that result.

## Technology does not determine power

Identifying characteristics compatible with LED or a legacy luminaire does not prove installed power. Never infer 80 W, 100 W, or any other value from appearance, brightness, color, or technology alone.

Installed power may be treated as verified only when it comes from an authoritative source, for example:

- a legible label on the luminaire, lamp, driver, or assembly;
- an unambiguously identified model checked against a trusted catalog;
- an authorized measurement or operational document;
- an explicit result from a system tool.

If technology differs and power is still unverified, request a photo of the label or model when safely accessible. If it is not, keep power unverified and route the inspection to review; do not pressure the technician or invent a number.

## Billing load and auxiliary equipment

For unmetered public-lighting points, the Brazilian regulatory estimate considers the point's total nominal load, including auxiliary equipment, and the applicable operating time. A 70 W high-pressure-sodium lamp with 30 W of auxiliary load therefore represents a total registered load of 100 W, not 70 W.

When field power has been verified, the load difference is:

`verified load − registered load`

A negative difference means the field asset appears to draw less power than the record; a positive difference means the opposite. LumaField may calculate an energy difference for a clearly identified fictional scenario, but must not convert it to Brazilian reais without an authoritative tariff and the other required parameters.

## How to interpret evidence

### General luminaire photo

It may support an approximate technology classification from optical-assembly shape, emitter distribution, housing, and color temperature. Color temperature is a strong clue, but not standalone proof: camera processing, white balance, time of day, and surroundings can change its appearance.

### Label or model photo

It complements the general photo. Look for rated power in watts, manufacturer, model, serial number, voltage, and driver or ballast information. A legible label may verify power even when the framing does not allow the complete luminaire to be classified.

### Disagreement between person and vision

If the human observation and visual assessment disagree, preserve the conflict and route it to manual review. Do not silently choose one source.

## Business states

- **Utility record confirmed:** record and field evidence are compatible; no registry update is required.
- **Registry discrepancy:** the physical asset differs from the system of record by technology, load, or existence.
- **Insufficient evidence:** there is not enough support to confirm or refute the record.
- **Manual review:** relevant sources conflict or a required fact cannot be verified.
- **Operational exception:** an asset is off, on outside the expected period, damaged, or missing; this may lead to maintenance and a separate analysis.

Modernization and replacement orders are possible downstream consequences. They are not the primary reason for the inspection and must be proposed only when operational and contractual rules returned by the backend allow them.

## Public references

- ANEEL, Normative Resolution No. 1,000/2021, especially Articles 468 and 471: https://www2.aneel.gov.br/cedoc/ren20211000.pdf
- ANEEL, public-lighting overview: https://www.gov.br/aneel/pt-br/assuntos/iluminacao-publica
- Municipality of Rio do Sul, Santa Catarina, Public Notice 092/2024: https://s3cache.dom.sc.gov.br/atos/2024/04/1713289160_edital_092.2024__iluminao_pblica_extrato.pdf
