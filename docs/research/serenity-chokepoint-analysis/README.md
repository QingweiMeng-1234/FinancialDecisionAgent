# Serenity Chokepoint Analysis Research Assets

This directory is the persistent research root for the `serenity-chokepoint-analysis` skill.

## Purpose

Store reusable, structured research assets for concrete themes in `Research mode`.

Do not treat this directory as a place for static company verdicts. It is for:
- theme cards
- supply-chain maps
- evidence logs
- monitoring triggers

## Layout

```text
docs/research/serenity-chokepoint-analysis/
├── README.md
└── themes/
    └── <theme-slug>/
        ├── theme-card.md
        ├── supply-chain-map.md
        ├── evidence-log.md
        └── monitoring-triggers.md
```

## Operating Rules

- Create or update research assets only in `Research mode`.
- Use concrete dates when recording checked facts.
- Preserve history instead of overwriting old evidence.
- Separate `fact`, `inference`, and `open-question`.
- Attach `confidence` to key judgments.

## Future Automation

A future Python helper may scaffold and update this directory structure, but that helper is not implemented in v1.
