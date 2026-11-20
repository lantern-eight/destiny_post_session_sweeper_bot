'''Stand-ins for the Bungie connection and game data, for command-level tests.'''

from __future__ import annotations

from dataclasses import dataclass, field

from src.bungie_api import BungieApiError
from src.plan import Definitions
from src.preferences import Preferences
from tests.account_fixtures import DEFINITIONS, FakeBungie
from tests.fixtures import PREFERENCES


@dataclass
class FakeConnection:
  fake: FakeBungie
  membership: dict = field(default_factory=lambda: {'membershipType': 3})
  # Profile fetches that fail with this Bungie error, counted from the next one.
  failing_fetches: list[str | None] = field(default_factory=list)

  @property
  def client(self) -> FakeBungie:
    return self.fake

  def fetch_profile(self, components: tuple[int, ...] | None = None) -> dict:
    if self.failing_fetches:
      error_status = self.failing_fetches.pop(0)
      if error_status:
        raise BungieApiError(f'GET profile failed: {error_status}', error_status)
    return self.fake.profile()

  def refresh_sign_in(self) -> None:
    return None


@dataclass
class FakeManifest:
  version: str = '244213.26.06.29.2000-1-bnet.65864'


@dataclass
class FakeGameData:
  manifest: FakeManifest = field(default_factory=FakeManifest)
  definitions: Definitions = field(
    default_factory=lambda: Definitions(items=DEFINITIONS, sets={}, plug_sets={})
  )
  vault_capacity: int = 1300
  voltron: dict = field(default_factory=dict)
  preferences: Preferences = PREFERENCES
