from django.urls import path

from placements.views import (
    CompanyPlacementDetailView,
    CompanyPlacementListCreateView,
    CompanyPlacementPublishView,
    PlacementByIdView,
    PlacementDetailView,
    PlacementFacetsView,
    PlacementListView,
    PlacementSaveView,
    SavedPlacementListView,
)

urlpatterns = [
    # Student and public discovery.
    path("placements/", PlacementListView.as_view(), name="placement-list"),
    path(
        "placements/facets/",
        PlacementFacetsView.as_view(),
        name="placement-facets",
    ),
    path(
        "students/me/saved-placements/",
        SavedPlacementListView.as_view(),
        name="saved-placement-list",
    ),
    path(
        "placements/<uuid:placement_id>/save/",
        PlacementSaveView.as_view(),
        name="placement-save",
    ),
    path(
        "placements/by-id/<uuid:placement_id>/",
        PlacementByIdView.as_view(),
        name="placement-by-id",
    ),
    path(
        "placements/<slug:slug>/",
        PlacementDetailView.as_view(),
        name="placement-detail",
    ),
    # Company management.
    path(
        "company/placements/",
        CompanyPlacementListCreateView.as_view(),
        name="company-placement-list",
    ),
    path(
        "company/placements/<uuid:pk>/publish/",
        CompanyPlacementPublishView.as_view(),
        name="company-placement-publish",
    ),
    path(
        "company/placements/<uuid:pk>/",
        CompanyPlacementDetailView.as_view(),
        name="company-placement-detail",
    ),
]
