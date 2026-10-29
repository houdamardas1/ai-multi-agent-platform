from autonomous_agent import IntelligentAutonomousAgent

agent = IntelligentAutonomousAgent()

result = agent.traiter_email_intelligent(
    "test@gmail.com",
    "support urgent",
    "j'ai un problème avec mon compte"
)

print(result)
print(agent.get_crm_stats())