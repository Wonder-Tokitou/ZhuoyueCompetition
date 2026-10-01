"""Bind shared completeness validation to the platform's teaching policy."""
from contracts import review
from . import rules

def required_dimensions(framework):
    return review.required_dimensions(rules.FRAMEWORK_DIMENSIONS[framework])

def output_contract(framework):
    return review.output_contract(rules.FRAMEWORK_DIMENSIONS[framework])

def validate_review(candidate, framework):
    return review.validate_review(candidate, rules.FRAMEWORK_DIMENSIONS[framework])
