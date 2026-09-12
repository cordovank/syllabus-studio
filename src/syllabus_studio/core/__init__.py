"""Course domain logic — models, prompts, and the three generation steps."""

from .builder import BuildError, build_course, outline_to_course
from .lessons import LessonError, write_lesson
from .models import Course, CourseSummary, Depth, Lesson, LessonContent, LessonProgress, Module
from .tutor import TutorError, ask, available_lenses, explain_with_lens

__all__ = [
    "BuildError",
    "Course",
    "CourseSummary",
    "Depth",
    "Lesson",
    "LessonContent",
    "LessonError",
    "LessonProgress",
    "Module",
    "TutorError",
    "ask",
    "available_lenses",
    "build_course",
    "explain_with_lens",
    "outline_to_course",
    "write_lesson",
]
