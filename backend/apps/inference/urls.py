"""混合推理控制面 URL。"""

from rest_framework.routers import DefaultRouter

from .views import (
    AIBudgetPolicyViewSet,
    BudgetReservationViewSet,
    BudgetSummaryViewSet,
    GenerationProfileViewSet,
    GenerationRouteViewSet,
    GenerationTargetViewSet,
    GenerationWorkItemViewSet,
    MediaArtifactViewSet,
    ProviderPriceRateViewSet,
    RuntimeNodeViewSet,
)


router = DefaultRouter()
router.register(r'runtime-nodes', RuntimeNodeViewSet, basename='runtime-node')
router.register(r'generation-profiles', GenerationProfileViewSet, basename='generation-profile')
router.register(r'generation-routes', GenerationRouteViewSet, basename='generation-route')
router.register(r'generation-route-targets', GenerationTargetViewSet, basename='generation-route-target')
router.register(r'provider-price-rates', ProviderPriceRateViewSet, basename='provider-price-rate')
router.register(r'budget-policies', AIBudgetPolicyViewSet, basename='budget-policy')
router.register(r'budget-reservations', BudgetReservationViewSet, basename='budget-reservation')
router.register(r'budget-summary', BudgetSummaryViewSet, basename='budget-summary')
router.register(r'generation-work-items', GenerationWorkItemViewSet, basename='generation-work-item')
router.register(r'media-artifacts', MediaArtifactViewSet, basename='media-artifact')

urlpatterns = router.urls
