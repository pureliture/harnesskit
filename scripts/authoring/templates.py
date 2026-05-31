from __future__ import annotations


def get_intent_template(work_id: str, kind: str) -> str:
    return f"""# Intent: {work_id}

## Goal

[Define the primary goal of this {kind} work item]

## Context

[Provide context about why this is being developed]

## Requirements Reference

[Link or list any references that inspire this work]
"""


def get_requirements_template(work_id: str, kind: str) -> str:
    return f"""# Requirements: {work_id}

## Capabilities

[List the target capabilities this {kind} must satisfy]

## Constraints

[Define runtime or design boundaries]

## Evidence Checkpoints

[Define how this {kind}'s fulfillment will be evaluated]
"""


def get_blueprint_template(work_id: str, kind: str) -> str:
    return f"""# Blueprint: {work_id}

## Design Philosophy

[Outline architectural decisions]

## Components Structure

[Define folder boundaries and managed blocks]

## Integration Strategy

[Describe how this components integrates into the target profile]
"""


def get_decision_log_template(work_id: str) -> str:
    return f"""# Decision Log: {work_id}

## Initial Conception

- **Date**: [Insert Date]
- **Status**: Conception
- **Decision**: Created private work item `{work_id}`.
"""
