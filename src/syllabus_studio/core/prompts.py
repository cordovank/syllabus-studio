"""Every prompt the app sends, in one file.

This is the file to edit when output quality needs work — nothing else needs to
change.  Each prompt opens with a ``[[SS:TASK]]`` marker so the offline provider
(and any logging or eval harness you add) can tell the tasks apart.
"""

from __future__ import annotations

from .models import Course, Depth, Lesson, LessonContent, Module

DEPTH_GUIDANCE: dict[Depth, str] = {
    "new": (
        "The learner is new to this material. Define every term on first use, keep each step "
        "small, and use concrete examples before abstractions."
    ),
    "standard": (
        "The learner has some general background but is new to this specific material. "
        "Be substantive but keep the pace brisk."
    ),
    "deep": (
        "The learner is experienced and wants rigour. Be precise and technical, include the "
        "underlying mechanism, assumptions, and failure modes. Do not pad."
    ),
}

LENSES: dict[str, dict[str, str]] = {
    "eli5": {
        "label": "Plain words",
        "ask": "Re-explain it with no jargon at all, as if to a smart person outside the field. "
        "Short sentences.",
    },
    "analogy": {
        "label": "By analogy",
        "ask": "Re-explain it through one concrete, everyday analogy carried all the way through, "
        "then say exactly where the analogy breaks down.",
    },
    "picture": {
        "label": "Draw it",
        "ask": "Re-explain it as a labelled text diagram inside a code block (boxes, arrows, a flow "
        "or a table), then three lines reading the diagram.",
    },
    "rigor": {
        "label": "Go deeper",
        "ask": "Re-explain it precisely and technically: the formal statement, the assumptions it "
        "needs, the edge cases, and one common mistake.",
    },
}

MAX_SYLLABUS_CHARS = 40_000
MAX_CONTEXT_CHARS = 6_000
MAX_LESSON_CONTEXT = 9_000


def outline_prompt(*, syllabus: str, name: str = "", depth: Depth = "standard") -> str:
    named = f'- The course is called "{name}". Use that as the title.\n' if name else ""
    return f"""[[SS:OUTLINE]]
You are designing a self-paced online course from a syllabus. Read the syllabus below and produce
the course structure.

{DEPTH_GUIDANCE[depth]}

Rules:
- 4 to 7 modules. Each module has 2 to 5 lessons.
- A lesson is one sitting: 8-16 minutes of reading plus practice.
- Lesson titles state the idea, not the week number. Never write "Week 3" or "Introduction to X".
- Every "hook" is one vivid sentence that makes the lesson feel worth opening.
- Cover what the syllabus actually covers. Do not invent topics it does not mention, and do not
  skip topics it does.
{named}
Reply with ONLY this JSON object and nothing else:
{{"title":"string","subtitle":"one clause, under 12 words","level":"beginner|intermediate|advanced",
"skills":["3-5 things the learner will be able to DO, each starting with a verb"],
"modules":[{{"title":"string","summary":"one sentence","outcomes":["2-4 short outcomes"],
"lessons":[{{"title":"string","hook":"one sentence","minutes":12}}]}}]}}

SYLLABUS:
{syllabus[:MAX_SYLLABUS_CHARS]}"""


def lesson_prompt(*, course: Course, module: Module, module_index: int, lesson: Lesson) -> str:
    siblings = "\n".join(
        f"{i + 1}. {ls.title}" + ("   <-- write this one" if ls.id == lesson.id else "")
        for i, ls in enumerate(module.lessons)
    )
    subtitle = f" — {course.subtitle}" if course.subtitle else ""
    summary = f" — {module.summary}" if module.summary else ""
    hook = f"ITS PROMISE TO THE LEARNER: {lesson.hook}\n" if lesson.hook else ""
    context = (
        f"\nSYLLABUS EXCERPT for context:\n{course.source[:MAX_CONTEXT_CHARS]}\n"
        if course.source
        else ""
    )

    return f"""[[SS:LESSON]]
You are writing one lesson of a self-paced course. Write it in full — this is the material the
learner reads, not a plan for it.

COURSE: {course.title}{subtitle}
MODULE {module_index + 1}: {module.title}{summary}
LESSONS IN THIS MODULE (stay in your lane, do not cover the others):
{siblings}

LESSON TO WRITE: {lesson.title}
{hook}TARGET LENGTH: about {lesson.minutes} minutes of reading.

{DEPTH_GUIDANCE[course.depth]}

Craft rules:
- Write prose that teaches. Concrete before abstract; a real example beats a definition.
- In each section body, wrap the single most important phrase in ==double equals== so it can be
  highlighted. Once per section, no more.
- You may use **bold**, `code`, - bullets, and ```fenced blocks``` inside body text. No headings
  inside body.
- The worked example must be a specific scenario with real numbers or real names, carried step by
  step.
- Quiz questions test judgement, not recall of a definition. Exactly one option is right; the wrong
  ones must be tempting. The 'why' explains why the right one is right AND why the tempting one is
  wrong.
- Practice tasks are things the learner does away from the screen, with their own material where
  possible.
- Never mention that you are an AI, and never refer to "this lesson" in the third person.
{context}
Reply with ONLY this JSON object and nothing else:
{{"bigIdea":"1-2 sentences: the thing to remember if everything else is forgotten",
"sections":[{{"heading":"short noun phrase","body":"2-5 paragraphs of teaching prose","note":"optional one-sentence aside, or empty string"}}],
"keyTerms":[{{"term":"string","definition":"one sentence"}}],
"worked":{{"title":"string","steps":["4-7 steps, each a full sentence"]}},
"quiz":[{{"q":"string","options":["a","b","c","d"],"answer":0,"why":"string"}}],
"practice":[{{"task":"string","hint":"string"}}]}}
Use 3-4 sections, 4-6 key terms, 3 quiz questions, 2 practice tasks."""


def _lesson_digest(content: LessonContent) -> str:
    parts = [content.big_idea]
    parts += [f"{s.heading}\n{s.body}" for s in content.sections]
    return "\n\n".join(p for p in parts if p)[:MAX_LESSON_CONTEXT]


def lens_prompt(*, course: Course, lesson: Lesson, content: LessonContent, lens: str) -> str:
    spec = LENSES[lens]
    return f"""[[SS:LENS]]
Here is a lesson a learner has just read but has not understood.

LESSON: {lesson.title}
COURSE: {course.title}

CONTENT:
{_lesson_digest(content)}

TASK: {spec["ask"]}
Keep it under 300 words. No preamble, no sign-off — start with the explanation itself.
Plain paragraphs; you may use **bold**, `code`, - bullets and ```fenced blocks```."""


def tutor_system_turn(*, course: Course, lesson: Lesson, content: LessonContent) -> str:
    return f"""[[SS:ASK]]
You are tutoring one learner through a specific lesson. Answer only from the lesson's subject; if
they ask about something the lesson does not cover, say so briefly and point at which part of the
course does.
Be direct and concrete. Under 200 words unless they ask for more. No preamble, no "great question".

COURSE: {course.title}
LESSON: {lesson.title}

LESSON CONTENT:
{_lesson_digest(content)}"""


def faq_prompt(*, course: Course, lesson: Lesson, content: LessonContent, count: int = 10) -> str:
    """Runs at authoring time on a strong model, so a reader with no model still gets
    answers written with the whole lesson in view."""
    return f"""[[SS:FAQ]]
Here is a lesson from a self-paced course. Write the questions a learner actually asks after
reading it, and answer each one.

LESSON: {lesson.title}
COURSE: {course.title}

CONTENT:
{_lesson_digest(content)}

Rules:
- Write {count} questions. Each is a real point of confusion, a tempting misreading, or an edge
  case the lesson raises but does not settle. Never a definition-recall question.
- Skip any question whose answer is already a sentence in the lesson above. The reader has that.
- Phrase questions the way a learner would ask them, in the first person where natural.
- Each answer is under 120 words, direct, and grounded in this lesson's subject. No preamble.
- You may use **bold**, `code` and - bullets inside answers.

Reply with ONLY this JSON object and nothing else:
{{"faq":[{{"q":"string","a":"string"}}]}}"""
