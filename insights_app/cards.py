from __future__ import annotations


CARD_COLOURS = ("Red", "Yellow", "Green", "Blue")


COLOUR_BEHAVIOURS = {
    "Red": [
        "I move quickly when a decision needs momentum.",
        "I am comfortable taking charge when the route is unclear.",
        "I like direct, practical conversations.",
        "I push for outcomes when energy starts to drift.",
        "I prefer a clear target and visible progress.",
        "I challenge delays when action would teach us faster.",
        "I value confidence, pace, and accountability.",
        "I enjoy turning discussion into next steps.",
        "I am willing to make the difficult call.",
        "I bring urgency when the team needs focus.",
    ],
    "Yellow": [
        "I bring energy and optimism into the room.",
        "I enjoy exploring possibilities before narrowing the options.",
        "I like to involve people and build enthusiasm.",
        "I can improvise when the plan is still forming.",
        "I notice opportunities that others may not yet see.",
        "I prefer lively conversations to long written detail.",
        "I help people feel included and encouraged.",
        "I enjoy starting new ideas and experiments.",
        "I can make work feel engaging and human.",
        "I often connect people, ideas, and possibilities quickly.",
    ],
    "Green": [
        "I create space for people to be heard.",
        "I value steady relationships and mutual trust.",
        "I notice how decisions affect the wider group.",
        "I prefer collaboration over unnecessary competition.",
        "I bring patience when people need time to process.",
        "I look for practical compromises that preserve goodwill.",
        "I help the team stay grounded and considerate.",
        "I am attentive to quieter voices in the room.",
        "I support others through change with calm consistency.",
        "I value loyalty, care, and shared ownership.",
    ],
    "Blue": [
        "I like to understand the facts before committing.",
        "I notice risks, gaps, and inconsistencies.",
        "I prefer clear standards and reliable evidence.",
        "I bring structure to complicated work.",
        "I am comfortable checking details others may miss.",
        "I value accuracy and thoughtful preparation.",
        "I ask questions that improve the quality of decisions.",
        "I help turn ideas into repeatable processes.",
        "I prefer calm analysis over rushed assumptions.",
        "I keep track of what has been agreed.",
    ],
}


def seed_cards() -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    for colour in CARD_COLOURS:
        base = COLOUR_BEHAVIOURS[colour]
        for index in range(50):
            stem = base[index % len(base)]
            variant = index // len(base) + 1
            rows.append(
                (
                    colour,
                    index + 1,
                    f"{stem} Seed card {index + 1:02d}, replace with final {colour} wording later.",
                )
            )
    return rows
