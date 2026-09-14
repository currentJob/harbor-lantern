"""Cached public weather and exchange rates."""

from fastapi import APIRouter

from harbor_lantern.api.deps import FxProviderDep, WeatherProviderDep
from harbor_lantern.api.schemas import FxResponseOut, WeatherResponseOut
from harbor_lantern.services.external.cache import CachedProvider

router = APIRouter(prefix="/api", tags=["external"])


def payload(provider: CachedProvider):
    result = provider.get()
    return {**(result.payload or {}), **result.as_meta()}


@router.get("/weather", response_model=WeatherResponseOut)
def weather(provider: WeatherProviderDep):
    return payload(provider)


@router.get("/fx", response_model=FxResponseOut)
def fx(provider: FxProviderDep):
    return payload(provider)
