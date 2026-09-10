# LumaField — field technician guide

This document contains public-lighting operational knowledge that may help answer questions during an inspection. It contains no record for any pole. Data about the current point, inspection state, orders, and references comes from LumaField tools; conversational behavior and safety belong in prompts, the Workflow, and tests.

## Common technician questions

### “What is the registered power?”

Nominal lamp power, auxiliary power, and total registered load are different fields. Values for a specific point must come from the authoritative utility record.

### “Why does the ballast matter?”

In discharge-lighting technologies, the ballast is auxiliary equipment and its loss may be part of the total load considered for the point. A “70 W lamp” and a “100 W registered load” can therefore both be true when the record includes 30 W of auxiliary load. Always use the values returned for the current asset.

### “Why do I need a photo of the entire luminaire?”

The general photo records physical context and supports an assessment of technology, optical assembly, housing, reflector, diffuser, and consistency with the point being visited. A label-only photo may verify power, but normally does not prove which complete assembly is installed.

### “Why do you need another photo?”

A new image adds value only when it closes an explicit gap. If the first photo supports technology but does not show power, a label or model may verify that field. If the image is distant or obstructed, another safe angle may improve the technology evidence.

### “I cannot see or reach the label.”

An inaccessible label does not verify power. Technology and power may have different evidence levels; load remains unverified, and the inspection may continue to manual review.

### “Doesn't the light color prove that it is high-pressure sodium?”

Amber or warm light is a relevant clue for certain legacy technologies, while white light and defined optical distribution may support LED. Camera processing, white balance, time of day, diffusers, and warm-temperature LEDs can mislead. Combine color with geometry, optics, reflector, and distribution; it never determines power.

### “The modernization order is complete. So it is already LED?”

Not necessarily. The order is operational context, not physical proof and not proof of a registry update. Keep separate what the order says, what the utility record contains, and what field evidence supports.

### “Can I continue without a photo?”

Without sufficient visual evidence, the correct result is insufficient evidence, later resumption, or manual review, depending on authoritative operational state. The absence of a photo neither confirms nor refutes the record.

### “What is still missing?”

Possible gaps include the field observation, general photo, verified power, action confirmation, or dispatch result. The current gap belongs to the inspection's authoritative state.

### “Is there already an order or reference?”

A prepared request is not a created order; a queued operation is not a confirmed reference. The existence and status of an order or reference belong to the operational record or a status query.

## Observable field vocabulary

The technician may report what is visible without diagnosing a cause:

- luminaire on during daylight;
- luminaire off during the expected operating period;
- flickering, intermittent operation, or cycling;
- loose or damaged arm, support, cover, diffuser, or luminaire;
- leaning, struck, or apparently unstable pole;
- exposed cables, conductors, or components;
- smoke, odor, sparks, flame, or abnormal noise;
- missing or illegible asset identifier;
- physical asset with no matching registry record;
- record with no asset found at the location;
- several nearby points showing the same behavior.

These observations do not establish a cause. “It is flickering” does not mean “the driver has failed”; “there are sparks” does not mean “a short circuit is confirmed”; “it is off” does not mean “there is no power.”

## Minimum collection by situation

### Registry reconciliation

- pole number;
- utility record returned by the system;
- technician observation;
- general photo;
- label or model when power is needed and safely accessible;
- automatically captured date, time, and GPS when available.

### Operational failure without immediate danger

- behavior observed now;
- one point or several points;
- visible physical damage;
- optional photo when safe;
- automatic time and location;
- control-center reference after confirmed dispatch.

### Uncertain registry identity

- number read or reason it is illegible;
- automatic GPS;
- wide photo of the point;
- nearby reference only when the technician offers it or it is necessary;
- never silently choose the “closest” registry record.

## Useful data in a complete survey

Public-lighting surveys may record technology, power, auxiliary load, manufacturer, model, serial number, luminous flux, color temperature, driver, ballast, relay/control, coordinates, operating condition, and intervention history. LumaField collects only the subset required for each decision; do not turn the conversation into a universal form.

## Public references

- ANEEL, Normative Resolution No. 1,000/2021: https://www2.aneel.gov.br/cedoc/ren20211000.pdf
- ANEEL, public-lighting overview: https://www.gov.br/aneel/pt-br/assuntos/iluminacao-publica
- Municipality of Rio do Sul, Santa Catarina, Public Notice 092/2024, especially network inventory and field survey requirements: https://s3cache.dom.sc.gov.br/atos/2024/04/1713289160_edital_092.2024__iluminao_pblica_extrato.pdf
