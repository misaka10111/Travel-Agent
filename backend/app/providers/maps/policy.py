"""Conservative P0 defaults, not a blanket license to store provider content."""

from typing import Literal

from app.schemas.common import ContractModel, ProviderName


class MapDataPolicy(ContractModel):
    provider: ProviderName
    render_on: Literal["amap", "google"]
    attribution: str
    persist_raw_response: bool = False
    persist_geometry: bool = False
    cache_enabled: bool = False
    persist_provider_place_id: bool = False
    policy_url: str
    note: str


def get_policy(provider: ProviderName) -> MapDataPolicy:
    if provider == "google":
        return MapDataPolicy(provider="google", render_on="google", attribution="Google Maps",
            persist_provider_place_id=True,
            policy_url="https://developers.google.com/maps/documentation/routes/policies",
            note="Route results displayed on a map require a Google map; P0 keeps content in memory only. Confirm applicable account terms before production.")
    return MapDataPolicy(provider="amap", render_on="amap", attribution="高德地图",
        policy_url="https://lbs.amap.com/home/terms/",
        note="Conservative project policy: no raw/geometry persistence or cache until applicable storage/display rights are confirmed.")
