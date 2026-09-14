"""Baseline 1: always recommend the most popular open attraction that fits
the remaining time. No personalization, no optimization."""


class PopularFirstBaseline:
    def build_plan(self, constraints, context):
        raise NotImplementedError
