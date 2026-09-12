from fastapi import APIRouter
from sqlalchemy import select

from app.deps import DbSession
from app.models import Category
from app.schemas import CategoriesResponse

router = APIRouter()


@router.get("/categories")
def get_categories(db: DbSession) -> CategoriesResponse:
    """Retorna el catàleg públic de categories ordenat per codi."""
    categories = db.scalars(select(Category).order_by(Category.code)).all()
    return CategoriesResponse(categories=categories)
