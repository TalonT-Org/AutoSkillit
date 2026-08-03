"""Backend compatibility is adapted from typed skill semantics at execution time.

Lexical skill-capability discovery deliberately has no backend-routing authority,
so this module registers no recipe rule. Source authenticity and semantic-schema
validation remain workspace responsibilities; the selected CodingAgentBackend
validates and adapts the resulting SkillSemanticPlan.
"""
