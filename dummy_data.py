import json
import sys
import time
import random
from google import genai
from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL = "gemini-3-flash-preview"


def chat_send(chat, message):
    """Send a message in a chat session and return the response text."""
    response = chat.send_message(message)
    return response.text.strip()

# ── Scale Testing: Staged Scenario Generation ──

STAGES = {
    1: {
        "date": "2026-02-01",
        "friend": (
            "You are a close friend catching up with Aru. Today is February 1, 2026. "
            "Ask him about his life, his studies at Stanford, "
            "his strict vegetarian diet, his hobbies like basketball and acoustic guitar, and his upcoming trip to Yosemite. "
            "CRITICAL RULE: NEVER say goodbye. Keep messages conversational to 1-2 sentences."
        ),
        "aru": (
            "You are Aru, a junior in Mechanical Engineering at Stanford. Today is February 1, 2026. "
            "You live on-campus in Wilbur Hall. "
            "You are a strict vegetarian and cook Indian food — you NEVER eat meat, chicken, or fish. "
            "You play basketball at Arrillaga gym every weekend, play acoustic guitar, "
            "and are in the robotics club building a competition bot. "
            "You're taking ME 101 (thermodynamics) and ME 131 (fluid mechanics). "
            "You are in perfect health and run 3 miles every morning. "
            "You are planning a spring break trip to Yosemite with your friend Jake in late March. "
            "IMPORTANT: When asked about diet, clearly state you are a strict vegetarian. "
            "When asked about health, clearly state you are perfectly healthy with no injuries. "
            "When asked about where you live, say Wilbur Hall on campus. "
            "CRITICAL RULE: NEVER say goodbye. Keep responses limited to 1-2 sentences."
        ),
        "curveballs": [
            "Ask Aru how the robotics club competition bot is going.",
            "Ask Aru if he's excited for the Yosemite trip in March.",
            "Ask Aru about his thermodynamics class ME 101.",
            "Ask Aru what vegetarian dishes he cooked recently."
        ]
    },
    2: {
        "date": "2026-03-01",
        "friend": (
            "You are a close friend chatting with Aru. Today is March 1, 2026 — about a month since you last spoke. "
            "Ask him about his recent ACL tear from basketball, how his diet has changed, "
            "his move to the University Ave apartment, his new interest in CS/ML, and his cancelled Yosemite trip. "
            "CRITICAL RULE: NEVER say goodbye. Keep messages 1-2 sentences."
        ),
        "aru": (
            "You are Aru. Today is March 1, 2026. A month has passed since early February. "
            "You tore your ACL on February 20 playing basketball at Arrillaga gym. "
            "You are on anti-inflammatory meds (started Feb 20, prescribed for 2 weeks until March 6). "
            "You are doing physical therapy 3 times per week and the doctor said NO sports for 2 months (until April 20). "
            "You can NOT run, play basketball, or do any physical activity. "
            "DIET CHANGE: You started eating chicken and fish because the doctor said you need more protein for recovery. "
            "You are NO LONGER vegetarian — you gave up vegetarianism entirely. "
            "You dropped ME 131 and switched your focus to CS and Machine Learning. You are now taking CS 229 (Machine Learning). "
            "You moved off-campus to an apartment on University Ave with your friend Jake. You no longer live in Wilbur Hall. "
            "You cancelled the Yosemite trip because of your injury. You are now planning a summer trip to Tokyo instead. "
            "IMPORTANT: When asked about diet, clearly state you STOPPED being vegetarian and now eat chicken and fish. "
            "When asked about health, clearly state you have a torn ACL, are on meds until March 6, and can't play sports until April 20. "
            "When asked about where you live, say University Ave apartment, NOT Wilbur Hall. "
            "CRITICAL RULE: NEVER say goodbye. Keep responses limited to 1-2 sentences."
        ),
        "curveballs": [
            "Ask Aru how his ACL is healing and when the doctor said he can play sports again.",
            "Ask Aru how CS 229 is compared to his mechanical engineering classes.",
            "Ask Aru how he likes his new apartment on University Ave.",
            "Ask Aru if he misses being vegetarian."
        ]
    },
    3: {
        "date": "2026-05-01",
        "friend": (
            "You are a close friend chatting with Aru. Today is May 1, 2026 — about two months since you last spoke. "
            "Ask him about his fully healed ACL, his new summer research internship at SAIL, "
            "his new bouldering hobby, and his upcoming Tokyo trip. "
            "CRITICAL RULE: NEVER say goodbye. Keep messages 1-2 sentences."
        ),
        "aru": (
            "You are Aru. Today is May 1, 2026. Two months have passed since early March. "
            "Your ACL is FULLY HEALED. The doctor cleared you on April 20. "
            "You stopped physical therapy and stopped taking all medications — you are completely healthy now. "
            "You are running again and even played some basketball last week. "
            "You got a summer research internship at the Stanford AI Lab (SAIL) under Prof. Fei-Fei Li, "
            "working on computer vision for robotics. You start in June. "
            "You QUIT playing guitar — you don't have time anymore. You picked up bouldering at the campus climbing wall instead. "
            "You now lead the software and perception team in the robotics club (not just a member anymore). "
            "Your Tokyo trip is confirmed for August — you booked flights and an Airbnb. "
            "You still live on University Ave with Jake. You are still eating chicken and fish (not vegetarian). "
            "IMPORTANT: When asked about health, clearly state your ACL is fully healed and you have no injuries or medications. "
            "When asked about hobbies, mention bouldering and that you QUIT guitar. "
            "When asked about work, mention the SAIL internship starting in June. "
            "CRITICAL RULE: NEVER say goodbye. Keep responses limited to 1-2 sentences."
        ),
        "curveballs": [
            "Ask Aru if he's excited for the Tokyo trip now that flights are booked.",
            "Ask Aru what he'll be working on at SAIL.",
            "Ask Aru how bouldering compares to basketball.",
            "Ask Aru if he misses playing guitar."
        ]
    },
    4: {
        "date": "2026-06-15",
        "friend": (
            "You are a close friend chatting with Aru. Today is June 15, 2026 — about six weeks since you last spoke. "
            "Ask him about his new internship offer from Tesla, his recent bout of food poisoning, "
            "his non-vegetarian meal prepping, and his new surfing hobby. Mix in some random everyday chat. "
            "CRITICAL RULE: NEVER say goodbye. Keep messages 1-2 sentences."
        ),
        "aru": (
            "You are Aru. Today is June 15, 2026. About six weeks have passed since early May. "
            "You got a competing internship offer from the Tesla Autopilot team. "
            "You decided to LEAVE SAIL and accepted the Tesla offer. You no longer work at SAIL. "
            "You relocated from University Ave to Palo Alto to be near the Tesla office. You no longer live on University Ave. "
            "You got food poisoning on June 10 and are on antibiotics for 5 days (until June 15). "
            "You are now fully non-vegetarian, meal-prepping chicken and rice every week. "
            "You picked up surfing at Pacifica beach on weekends. You still do bouldering occasionally. "
            "The Tokyo trip in August is still happening. "
            "Mix in regular routine talk about weather, assignments, and movies when appropriate. "
            "IMPORTANT: When asked about work, clearly state you LEFT SAIL and now work at Tesla Autopilot in Palo Alto. "
            "When asked about where you live, say Palo Alto, NOT University Ave. "
            "When asked about health, mention the food poisoning and antibiotics ending June 15. "
            "CRITICAL RULE: NEVER say goodbye. Keep responses limited to 1-2 sentences."
        ),
        "curveballs": [
            "Ask Aru how the transition from SAIL to Tesla is going.",
            "Ask Aru if the antibiotics for food poisoning are helping.",
            "Ask Aru if he's caught any good waves surfing at Pacifica.",
            "Ask Aru what movies he's watched recently."
        ]
    }
}

def generate_staged_conversation(stage_num: int, total_messages: int = 100):
    stage_config = STAGES.get(stage_num)
    if not stage_config:
        print(f"Unknown stage: {stage_num}")
        return

    friend_chat = client.chats.create(
        model=MODEL,
        config={"system_instruction": stage_config["friend"]},
    )
    aru_chat = client.chats.create(
        model=MODEL,
        config={"system_instruction": stage_config["aru"]},
    )

    conversation_log = []
    turns = total_messages // 2

    stage_date = stage_config.get("date")
    friend_message = f"Hey Aru! It's been a while since we talked. How are things?"

    print(f"\nGenerating Stage {stage_num} ({total_messages} messages, date={stage_date})...")

    try:
        for i in range(turns):
            conversation_log.append({"role": "user", "content": friend_message})
            print(f"  [{2*i+1}/{total_messages}] Friend: {friend_message[:60]}...")

            aru_message = chat_send(aru_chat, friend_message)
            conversation_log.append({"role": "assistant", "content": aru_message})
            print(f"  [{2*i+2}/{total_messages}] Aru: {aru_message[:60]}...")

            time.sleep(0.5)

            if i < turns - 1:
                # Inject a curveball
                if i % 15 == 0 and i > 0:
                    injection = random.choice(stage_config["curveballs"])
                    secret_prompt = f"(System note: Change the subject. {injection})"
                    friend_message = chat_send(friend_chat, secret_prompt)
                else:
                    friend_message = chat_send(friend_chat, aru_message)

                time.sleep(0.5)

    except Exception as e:
        print(f"\nSimulation interrupted: {e}")
        print(f"  Saved {len(conversation_log)} messages before error.")

    output = {
        "source_id": f"stage_{stage_num}",
        "conversation": conversation_log,
    }
    if stage_date:
        output["date"] = stage_date
    
    filename = f"stage_{stage_num}_messages.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(conversation_log)} messages to {filename}")
    return filename

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "scenario":
        name = sys.argv[2] if len(sys.argv) > 2 else "all"
        if name == "all":
            for scenario_name in SCENARIOS:
                run_scenario(scenario_name)
        else:
            run_scenario(name)

    elif command == "stage":
        stage_num = int(sys.argv[2])
        count = int(sys.argv[3]) if len(sys.argv) > 3 else 100
        generate_staged_conversation(stage_num, count)

    else:
        print(f"Unknown command: {command}")
        print(__doc__)

if __name__ == "__main__":
    main()
