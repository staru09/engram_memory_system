# Result Summary — Scale Evaluation (All Stages)

Consolidated query results across all 4 stages for easy verification.

---

# Stage 1 — Baseline (100 msgs, query time: 2026-02-01)

| Metric | Value |
|--------|-------|
| MemCells | 10 |
| MemScenes | 3 |
| Active Facts | 111 |
| Total Facts | 111 |
| Conflicts | 0 |
| Dedup Rate | 0.0% |
| Queries Sufficient | 6/6 (100%) |
| Avg Latency | 9.0s |

---

## Q1: What does Aru study and where?
**Category:** Basic Extraction
**Expected:** Junior in Mechanical Engineering at Stanford. Taking ME 101 (thermodynamics) and ME 131 (fluid mechanics).
**Status:** SUFFICIENT | Rounds: 1 | Latency: 8.7s
**Retrieved:** 3 episodes, 10 facts, 40 active foresight

**Generated Answer:** Aru is a student at Stanford University, where he resides in Wilbur Hall. He is currently enrolled in ME 101 (Thermodynamics) and ME 131 (Fluid Mechanics) and is also building a competition robot for a robotics club.

---

## Q2: What kind of diet does Aru follow?
**Category:** Basic Extraction
**Expected:** Strict vegetarian. Cooks Indian food (dal, sabzi, paneer). Never eats meat, chicken, or fish.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 7.7s
**Retrieved:** 3 episodes, 10 facts, 40 active foresight

**Generated Answer:** Aru follows a strict vegetarian diet that excludes meat, chicken, and fish. He primarily eats Indian cuisine, often cooking dishes such as aloo paratha, dal, vegetable biryani, and spicy chickpea curry.

---

## Q3: Where does Aru currently live?
**Category:** Basic Extraction
**Expected:** Wilbur Hall, on-campus at Stanford.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 7.3s
**Retrieved:** 3 episodes, 10 facts, 40 active foresight

**Generated Answer:** Aru currently resides in Wilbur Hall while attending Stanford University. He frequently cooks vegetarian Indian meals and practices his acoustic guitar at this residence.

---

## Q4: What are Aru's hobbies and interests?
**Category:** Scene Formation
**Expected:** Basketball (Arrillaga gym weekends), acoustic guitar, robotics club (building competition bot). Runs 3 miles daily.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.9s
**Retrieved:** 3 episodes, 10 facts, 40 active foresight

**Generated Answer:** Aru's hobbies include playing fingerstyle and folk songs on the acoustic guitar, building competition robots, and cooking Indian vegetarian dishes like aloo paratha and dal. He also enjoys physical activities such as running three miles every morning, playing basketball on weekends, and hiking in national parks like Yosemite.

---

## Q5: Does Aru have any upcoming travel plans?
**Category:** Scene Formation
**Expected:** Spring break trip to Yosemite planned with friend Jake in late March.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 8.9s
**Retrieved:** 3 episodes, 10 facts, 40 active foresight

**Generated Answer:** Aru is planning a spring break trip to Yosemite National Park with a friend named Jake. They plan to hike the Mist Trail, Half Dome, and Upper Yosemite Fall while staying active with activities like playing guitar by the campfire. To prepare, Aru is monitoring snow reports and preparing portable vegetarian meals like dry aloo parathas.

---

## Q6: What do you know about Aru overall?
**Category:** Simple Retrieval
**Expected:** Stanford ME junior, lives in Wilbur Hall, strict vegetarian, plays basketball and guitar, in robotics club, planning Yosemite trip.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.7s
**Retrieved:** 3 episodes, 10 facts, 40 active foresight

**Generated Answer:** Aru is a disciplined Stanford University student living in Wilbur Hall who balances a mechanical engineering workload with a competition robotics project and a daily routine of morning runs. He is a strict vegetarian who cooks Indian food and practices acoustic guitar daily to manage stress and stay culturally connected. Currently in perfect health, Aru is planning a spring break hiking trip to Yosemite National Park with his friend Jake.

---
---

# Stage 2 — Conflicts + Foresight (200 msgs cumulative, query time: 2026-03-01)

| Metric | Value |
|--------|-------|
| MemCells | 20 |
| MemScenes | 3 |
| Active Facts | 209 |
| Total Facts | 223 |
| Conflicts | 14 |
| Dedup Rate | 6.3% |
| Queries Sufficient | 8/10 (80%) |
| Avg Latency | 14.2s |

---

## Q1: What kind of diet does Aru currently follow?
**Category:** Conflict Detection
**Expected:** No longer vegetarian. Now eats chicken and fish (doctor's orders for ACL recovery protein). Conflict should supersede vegetarian.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.6s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** Aru has transitioned from strict vegetarianism to a high-protein diet that includes chicken and fish to aid in his recovery from an ACL injury. He also continues to cook Indian dishes such as aloo paratha, dal, and spicy chickpea curry, often with help from his roommate Jake.

---

## Q2: Where is Aru currently working or interning?
**Category:** Conflict Detection
**Expected:** No internship yet. Still a student but pivoted from ME to CS/ML (taking CS 229).
**Status:** INSUFFICIENT | Rounds: 2 | Latency: 22.8s
**Retrieved:** 4 episodes, 12 facts, 82 active foresight

**Generated Answer:** No relevant information found.

---

## Q3: What is Aru's academic and internship history?
**Category:** Profile Evolution
**Expected:** Was ME student (ME 101, ME 131). Dropped ME 131, now taking CS 229 (Machine Learning). Pivoting to CS/ML.
**Status:** INSUFFICIENT | Rounds: 2 | Latency: 24.8s
**Retrieved:** 4 episodes, 13 facts, 82 active foresight

**Generated Answer:** Aru is a Stanford University student whose academic focus shifted from Mechanical Engineering to Computer Science and Machine Learning, including courses like ME 101 and CS 229. Their academic work involves building a competition robot and completing a reinforcement learning project on NBA player efficiency. No relevant information was found regarding Aru's internship history.

---

## Q4: Does Aru have any active health issues or injuries?
**Category:** Foresight / Temporal
**Expected:** Torn ACL (Feb 20). Doing physical therapy 3x/week. No sports for 2 months (until April 20).
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.5s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** Aru is currently recovering from a torn ACL sustained while playing basketball on February 20, 2026. He is undergoing physical therapy three times a week and taking anti-inflammatory medication through March 6, 2026. Consequently, he is restricted from sports and running until April 20, 2026.

---

## Q5: Is Aru currently on any medication?
**Category:** Foresight / Temporal
**Expected:** Anti-inflammatory meds (started Feb 20, prescribed 2 weeks until March 6). Should still be active at query time March 1.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.8s
**Retrieved:** 10 episodes, 10 facts, 82 active foresight

**Generated Answer:** Yes, Aru is currently taking anti-inflammatory medication to assist with recovery from a torn ACL sustained in February 2026. This course of medication is scheduled to continue until March 6, 2026.

---

## Q6: What are Aru's hobbies and interests?
**Category:** Retrieval Relevance
**Expected:** Robotics club (still active). Guitar (still active, not contradicted). No basketball (ACL injury). Can't run or do sports.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.5s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** Aru's hobbies include playing fingerstyle and folk songs on the acoustic guitar and cooking traditional Indian dishes such as aloo paratha and biryani. He is technically interested in robotics and machine learning, specifically applying these fields to projects like building competition robots and analyzing NBA player movement data. Additionally, he enjoys active pursuits like basketball, running, and hiking, though these are currently restricted due to an ACL injury.

---

## Q7: Does Aru have any upcoming travel plans?
**Category:** Retrieval Relevance
**Expected:** Yosemite cancelled due to injury. Planning summer trip to Tokyo instead.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 12.3s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** Aru canceled a planned spring break trip to Yosemite National Park due to an ACL injury sustained in February 2026. He is currently planning a replacement trip to Tokyo with his roommate Jake for Summer 2026, which will include visiting Akihabara and hiking Mt. Takao.

---

## Q8: Where does Aru currently live?
**Category:** Profile Evolution
**Expected:** University Ave apartment (off-campus) with Jake. No longer in Wilbur Hall. Conflict should supersede.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 8.8s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** Aru currently resides in an apartment on University Avenue with his roommate, Jake. He recently moved to this location from Wilbur Hall following an ACL injury sustained in February 2026.

---

## Q9: Can Aru play sports this week?
**Category:** Foresight / Temporal
**Expected:** No. Torn ACL, doctor restricted all sports for 2 months (until April 20). Cannot run or play basketball.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 11.5s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** No, Aru cannot play sports this week because they are recovering from a torn ACL sustained on February 20, 2026. According to their medical restrictions, Aru is prohibited from participating in sports and running until April 20, 2026.

---

## Q10: What do you know about Aru overall?
**Category:** Retrieval Relevance
**Expected:** Stanford student, torn ACL recovery, pivoted to CS/ML, moved to University Ave, no longer vegetarian, Tokyo trip planned.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.9s
**Retrieved:** 4 episodes, 10 facts, 82 active foresight

**Generated Answer:** Aru is a resilient Stanford student who recently shifted his academic focus from Mechanical Engineering to Computer Science and Machine Learning while recovering from a torn ACL sustained in February 2026. He lives on University Avenue with his roommate Jake, with whom he cooks high-protein Indian meals and plans a summer trip to Tokyo. Outside of his studies and physical therapy, Aru works on robotics and machine learning projects and plays fingerstyle folk songs on the acoustic guitar.

---
---

# Stage 3 — Foresight Expiry + Evolution (300 msgs cumulative, query time: 2026-05-01)

| Metric | Value |
|--------|-------|
| MemCells | 30 |
| MemScenes | 3 |
| Active Facts | 313 |
| Total Facts | 333 |
| Conflicts | 20 |
| Dedup Rate | 6.0% |
| Queries Sufficient | 10/10 (100%) |
| Avg Latency | 13.5s |

---

## Q1: What kind of diet does Aru currently follow?
**Category:** Conflict Detection
**Expected:** Still eating chicken and fish (not vegetarian). No new diet change in stage 3.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.4s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru has transitioned from a strict vegetarian diet to one that includes chicken and fish, such as salmon, to increase his protein intake. He currently maintains this non-vegetarian diet to aid in muscle recovery while continuing to cook Indian food.

---

## Q2: Where is Aru currently working or interning?
**Category:** Conflict Detection
**Expected:** Summer research internship at Stanford AI Lab (SAIL) under Prof. Fei-Fei Li. Computer vision for robotics. Starting June.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 14.6s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru is currently leading the robotics software team, where they are documenting a perception stack and training a replacement for a hand-off. Additionally, Aru is scheduled to begin a research internship at the Stanford Artificial Intelligence Laboratory (SAIL) in June 2026.

---

## Q3: What is Aru's academic and internship history?
**Category:** Profile Evolution
**Expected:** ME student → dropped ME 131 → CS 229 pivot → SAIL internship (computer vision for robotics).
**Status:** SUFFICIENT | Rounds: 1 | Latency: 21.9s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru is a Stanford University student who pivoted from Mechanical Engineering to Computer Science and Machine Learning, with current coursework including CS 229 and ME 101. They lead the robotics software team and will begin a research internship at the Stanford Artificial Intelligence Laboratory (SAIL) in June 2026 under Prof. Fei-Fei Li. This internship focuses on computer vision for robotics, specifically Vision Transformers and visual perception for dexterous manipulation.

---

## Q4: Does Aru have any active health issues or injuries?
**Category:** Foresight / Temporal
**Expected:** No. ACL fully healed (cleared April 20). Stopped physical therapy. Running again. No injuries.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 11.4s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru does not have any active health issues or injuries and is currently 100% healthy. He has fully recovered from an ACL injury sustained in February 2026 and is no longer taking any medications. As of May 2026, he has resumed physical activities including basketball, daily three-mile runs, and bouldering.

---

## Q5: Is Aru currently on any medication?
**Category:** Foresight / Temporal
**Expected:** No. Anti-inflammatory meds expired (ended March 6). No current medication.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 11.2s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru is not currently taking any medication. As of May 2026, he is 100% healthy and has fully recovered from his previous ACL injury.

---

## Q6: What are Aru's hobbies and interests?
**Category:** Retrieval Relevance
**Expected:** Bouldering (new), robotics club (leads software/perception team). Quit guitar. Back to running. Basketball possible again.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 12.8s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru's primary hobbies include bouldering at a V5 grade level, playing basketball, and going on daily three-mile runs. He is deeply interested in robotics and machine learning, specifically focusing on vision transformers for manipulation and analyzing NBA player movement data. Additionally, he enjoys cooking Indian food and traveling to explore tech exhibits and outdoor bouldering spots.

---

## Q7: Does Aru have any upcoming travel plans?
**Category:** Retrieval Relevance
**Expected:** Tokyo trip confirmed for August. Flights and Airbnb booked.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 12.0s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru has a scheduled trip to Tokyo with his roommate Jake in August 2026, for which flights and an Airbnb are already booked. The itinerary includes visiting bouldering gyms, exploring tech and robotics exhibits in Akihabara, and hiking Mt. Takao. He also plans to go bouldering at Castle Rock before starting his internship in June 2026.

---

## Q8: Where does Aru currently live?
**Category:** Profile Evolution
**Expected:** Still on University Ave apartment with Jake.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.5s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru currently resides in an apartment on University Avenue with his roommate, Jake. While some earlier records mention him living in Wilbur Hall, more recent information indicates he moved to the University Avenue location.

---

## Q9: Can Aru play sports this week?
**Category:** Foresight / Temporal
**Expected:** Yes. ACL fully healed, doctor cleared, back to running and basketball.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 14.1s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Yes, Aru can play sports this week as their ACL injury is fully healed and they are 100% healthy as of May 2026. Aru has already resumed daily three-mile runs, bouldering at a V5 grade, and playing basketball at the Arrillaga gym every weekend.

---

## Q10: What do you know about Aru overall?
**Category:** Retrieval Relevance
**Expected:** Stanford CS/ML student, ACL healed, SAIL internship, bouldering, quit guitar, lives on University Ave, Tokyo trip in August.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 16.0s
**Retrieved:** 5 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru is a Stanford student and robotics software lead who transitioned from Mechanical Engineering to Computer Science and is preparing for a research internship at SAIL. Following a full recovery from an ACL injury, he maintains an active lifestyle through V5-level bouldering and daily runs while following a high-protein diet that includes chicken and fish. He resides on University Avenue with his roommate Jake, with whom he is planning an extensive trip to Tokyo in August 2026.

---
---

# Stage 4 — Scale + Noise (500 msgs cumulative, query time: 2026-06-15)

| Metric | Value |
|--------|-------|
| MemCells | 60 |
| MemScenes | 4 |
| Active Facts | 558 |
| Total Facts | 582 |
| Conflicts | 24 |
| Dedup Rate | 4.1% |
| Queries Sufficient | 10/10 (100%) |
| Avg Latency | 12.8s |

---

## Q1: What kind of diet does Aru currently follow?
**Category:** Conflict Detection
**Expected:** Fully non-vegetarian. Meal-preps chicken and rice. No change from stage 2+ but reinforced.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 12.7s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru currently follows a non-vegetarian diet, having transitioned from being a strict vegetarian to eating chicken and fish to increase his protein intake for injury recovery. He regularly meal-preps chicken and rice, though he has recently increased his focus on food safety following a bout of food poisoning. He is also planning to explore non-vegetarian food tours during an upcoming trip to Tokyo.

---

## Q2: Where is Aru currently working or interning?
**Category:** Conflict Detection
**Expected:** Tesla Autopilot team. Left SAIL. Conflict should supersede SAIL internship.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.1s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru is currently working as an intern at Tesla Autopilot in Palo Alto. As of June 15, 2026, he has been in this role for six weeks after transitioning from his previous position at the Stanford Artificial Intelligence Laboratory (SAIL).

---

## Q3: What is Aru's academic and internship history?
**Category:** Profile Evolution
**Expected:** ME courses → CS 229 pivot → SAIL research → Tesla Autopilot. Full progression visible.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.2s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru is a Stanford University student who transitioned from Mechanical Engineering to Computer Science and Machine Learning, completing academic projects like an NBA player efficiency analysis using Reinforcement Learning. His professional history includes leading the robotics software team and researching computer vision at the Stanford Artificial Intelligence Laboratory (SAIL) before starting his current internship at Tesla Autopilot. As of June 2026, he has been interning with the Tesla Autopilot team in Palo Alto for six weeks.

---

## Q4: Does Aru have any active health issues or injuries?
**Category:** Foresight / Temporal
**Expected:** Food poisoning (June 10). On antibiotics for 5 days (until June 15). Depends on query time whether active.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.6s
**Retrieved:** 6 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru has fully recovered from an ACL injury sustained in February 2026. However, as of June 15, 2026, Aru is finishing a course of antibiotics for food poisoning and is in a temporary recovery period.

---

## Q5: Is Aru currently on any medication?
**Category:** Foresight / Temporal
**Expected:** Antibiotics for food poisoning (5 days, June 10-15). Should be active/expiring at query time June 15.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 11.0s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** As of June 15, 2026, Aru is currently finishing a course of antibiotics to treat food poisoning caused by chicken meal prep. Today marks the final day of this medication course. While Aru previously recovered from an ACL injury, he is no longer taking any medication related to that condition.

---

## Q6: What are Aru's hobbies and interests?
**Category:** Retrieval Relevance
**Expected:** Surfing at Pacifica (new), bouldering (occasional), robotics club. No guitar. No basketball mentioned recently.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.2s
**Retrieved:** 6 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru's primary hobbies include V5-level bouldering, solo surfing at Pacifica, daily three-mile runs, and weekend basketball. He is also interested in robotics, machine learning projects such as NBA player analytics, and watching sci-fi movies, though he recently quit playing guitar to focus on these pursuits. During his upcoming travel to Tokyo, he plans to explore tech districts and participate in non-vegetarian food tours.

---

## Q7: Does Aru have any upcoming travel plans?
**Category:** Retrieval Relevance
**Expected:** Tokyo trip in August still planned.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.9s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru has a scheduled trip to Tokyo with his roommate Jake in August 2026, for which flights and an Airbnb are already booked. The plans include visiting tech districts like Akihabara and Miraikan, exploring local bouldering gyms, and researching non-vegetarian food tours. Additionally, he plans to travel to Pacifica for a solo surfing session this upcoming weekend.

---

## Q8: Where does Aru currently live?
**Category:** Profile Evolution
**Expected:** Palo Alto (relocated for Tesla). No longer on University Ave. Conflict should supersede.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 10.7s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru currently resides in an apartment on University Avenue in Palo Alto. He lives there with his roommate, Jake.

---

## Q9: Can Aru play sports this week?
**Category:** Foresight / Temporal
**Expected:** Yes. No sports-related injuries. Food poisoning doesn't restrict sports.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 20.8s
**Retrieved:** 6 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru can likely play sports this week because his ACL injury is fully healed and he is finishing his course of antibiotics for food poisoning on June 15, 2026. Although he is in a monitored recovery period until June 22, he has specific plans to go surfing at Pacifica this weekend and typically maintains a routine of daily runs and weekend basketball.

---

## Q10: What do you know about Aru overall?
**Category:** Retrieval Relevance
**Expected:** Stanford student interning at Tesla Autopilot, lives in Palo Alto, non-vegetarian, surfs at Pacifica, Tokyo trip in August.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 14.2s
**Retrieved:** 8 episodes, 10 facts, 133 active foresight

**Generated Answer:** Aru is a Stanford student and Tesla Autopilot intern who transitioned from mechanical engineering to focus on computer science and machine learning. He is a disciplined athlete who enjoys bouldering, surfing, and running, having successfully recovered from both a 2026 ACL injury and a recent bout of food poisoning. Residing in Palo Alto with his roommate Jake, Aru is currently preparing for a tech and bouldering-focused trip to Tokyo scheduled for August 2026.
