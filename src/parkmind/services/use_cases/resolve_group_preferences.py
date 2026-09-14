"""Use case: thin wrapper around GroupPreferenceResolver."""


class ResolveGroupPreferencesUseCase:
    def __init__(self, group_preference_resolver):
        self.group_preference_resolver = group_preference_resolver

    def execute(self, guest_profiles):
        raise NotImplementedError
