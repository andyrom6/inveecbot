from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger('InvexBot')

class ConversationManager:
    def __init__(self):
        self.conversations = {}
        self.expiry_time = timedelta(minutes=30)  # Conversation expires after 30 minutes
        self.achievements = {
            'first_chat': ' First Chat',
            'budget_set': ' Budget Planner',
            'first_sale': ' First Sale',
            'quick_response': ' Speed Demon',
            'bulk_buyer': ' Bulk Master',
            'profit_maker': ' Profit Pro',
            'feedback_king': ' Feedback King'
        }
        
    def get_user_context(self, user_id: str) -> dict:
        """Get or create user context."""
        now = datetime.now()
        
        # Clean up expired conversations
        self._cleanup_expired()
        
        # Get or create user conversation
        if user_id not in self.conversations:
            self.conversations[user_id] = {
                'last_updated': now,
                'context': {
                    'budget': None,
                    'interests': [],
                    'experience_level': None,
                    'previous_purchases': [],
                    'conversation_stage': 'initial',
                    'last_topic': None,
                    'should_promote_invexpro': False,
                    'promotion_context': '',
                    'achievements': [],
                    'sales_count': 0,
                    'total_profit': 0,
                    'positive_feedback': 0,
                    'avg_response_time': 3600,
                    'bulk_purchases': 0,
                    'response_rate': 0
                },
                'history': []
            }
        else:
            self.conversations[user_id]['last_updated'] = now
            
        return self.conversations[user_id]['context']
    
    def update_context(self, user_id: str, updates: dict):
        """Update user context with new information."""
        if user_id in self.conversations:
            self.conversations[user_id]['context'].update(updates)
            self.conversations[user_id]['last_updated'] = datetime.now()
            logger.info(f"Updated context for user {user_id}: {updates}")
    
    def add_to_history(self, user_id: str, message: str, is_bot: bool = False):
        """Add a message to the conversation history."""
        if user_id in self.conversations:
            self.conversations[user_id]['history'].append({
                'timestamp': datetime.now(),
                'message': message,
                'is_bot': is_bot
            })
            self.conversations[user_id]['last_updated'] = datetime.now()
    
    def get_conversation_history(self, user_id: str, limit: int = 5) -> list:
        """Get recent conversation history."""
        if user_id in self.conversations:
            return self.conversations[user_id]['history'][-limit:]
        return []
    
    def _cleanup_expired(self):
        """Remove expired conversations."""
        now = datetime.now()
        expired = [
            user_id for user_id, data in self.conversations.items()
            if now - data['last_updated'] > self.expiry_time
        ]
        for user_id in expired:
            del self.conversations[user_id]
            logger.info(f"Cleaned up expired conversation for user {user_id}")
    
    def get_next_question(self, user_id: str) -> str:
        """Get the next question to ask based on conversation stage."""
        context = self.get_user_context(user_id)
        stage = context.get('conversation_stage', 'initial')
        
        # Simple, focused questions for each stage
        questions = {
            'initial': "Hey! What's your budget for starting out? ",
            
            'budget_set': {
                'low': "Perfect! With ${budget}, I recommend starting with AirPods - they're only $10.90 to buy and you can sell them for $60-80! Want me to explain how? ",
                'medium': "Nice! ${budget} is a good start. Are you interested in electronics like AirPods, or fashion items like designer clothes? ",
                'high': "Awesome! ${budget} gives you lots of options. What catches your interest more: electronics, fashion, or luxury items? "
            },
            
            'interests_set': "Have you sold anything like this before? ",
            
            'experience_set': {
                'beginner': "No worries! Everyone starts somewhere. Want some tips on how to get your first sale? ",
                'intermediate': "Great experience! Ready to learn some pro strategies to boost your profits? ",
                'advanced': "Impressive! Would you like to explore some advanced scaling techniques? "
            },
            
            'follow_up': {
                'default': "What specific part would you like to know more about? ",
                'product': "Want to see the current best-selling items in this category? ",
                'pricing': "Would you like some pricing strategies for maximum profit? ",
                'supplier': "Should I tell you about our exclusive supplier network? "
            }
        }
        
        # Get appropriate question based on stage and context
        if stage == 'budget_set':
            budget = context.get('budget', 0)
            if budget <= 20:  # More specific low budget handling
                question = questions['budget_set']['low']
            elif budget < 200:
                question = questions['budget_set']['medium']
            else:
                question = questions['budget_set']['high']
            question = question.replace('${budget}', f'${budget}')
            
        elif stage == 'experience_set':
            exp_level = context.get('experience_level', 'beginner')
            question = questions['experience_set'].get(exp_level, questions['experience_set']['beginner'])
            
        elif stage == 'follow_up':
            last_topic = context.get('last_topic', 'default')
            question = questions['follow_up'].get(last_topic, questions['follow_up']['default'])
            
        else:
            question = questions.get(stage, questions['follow_up']['default'])
        
        # Add InvexPro promotion if appropriate
        if context.get('should_promote_invexpro'):
            promotion_context = context.get('promotion_context', '')
            if stage in ['experience_set', 'follow_up']:
                # Make promotion more casual and integrated
                promotions = {
                    'track your inventory': "BTW, our InvexPro app makes tracking inventory super easy! Want to check it out? ",
                    'track packages': "Quick tip: our InvexPro app can handle all your tracking needs! Interested? ",
                    'access exclusive suppliers': "Hey, want access to our exclusive supplier network through InvexPro? ",
                    'scale your business': "Ready to scale up? Our InvexPro app can help with that! Want to learn more? ",
                    'calculate profits': "BTW, InvexPro can auto-calculate all your profits! Interested? ",
                    'manage customer': "Pro tip: InvexPro makes customer management a breeze! Want to see how? "
                }
                
                for key, promo in promotions.items():
                    if key in promotion_context:
                        return promo
        
        return question

    def analyze_message(self, user_id: str, message: str) -> dict:
        """Analyze user message for context updates and promotion opportunities."""
        message_lower = message.lower()
        updates = {}
        
        # Check for InvexPro promotion triggers
        promotion_triggers = {
            'inventory': 'track your inventory and manage stock levels',
            'tracking': 'track packages, sales, and customer data',
            'supplier': 'access exclusive suppliers with 2-day delivery',
            'scaling': 'scale your business with automated tools',
            'profit': 'calculate profits and track expenses',
            'customer': 'manage customer relationships',
            'shipping': 'track shipments and manage deliveries'
        }
        
        for trigger, context in promotion_triggers.items():
            if trigger in message_lower:
                updates['should_promote_invexpro'] = True
                updates['promotion_context'] = context
        
        # Budget detection
        if any(word in message_lower for word in ['budget', 'spend', 'invest', '$']):
            import re
            amounts = re.findall(r'\$?(\d+(?:\.\d{2})?)', message)
            if amounts:
                updates['budget'] = float(amounts[0])
                updates['conversation_stage'] = 'budget_set'
                
                # If budget indicates serious business, suggest InvexPro
                if float(amounts[0]) > 100:
                    updates['should_promote_invexpro'] = True
                    updates['promotion_context'] = 'scale your business efficiently'
        
        # Interest detection
        product_keywords = {
            'electronics': ['electronics', 'airpods', 'phones', 'gadgets', 'tech'],
            'fashion': ['clothes', 'fashion', 'shoes', 'apparel', 'wear'],
            'accessories': ['accessories', 'watches', 'jewelry', 'bags']
        }
        
        for category, keywords in product_keywords.items():
            if any(word in message_lower for word in keywords):
                context = self.get_user_context(user_id)
                interests = context.get('interests', [])
                if category not in interests:
                    interests.append(category)
                    updates['interests'] = interests
                    updates['conversation_stage'] = 'interests_set'
        
        # Experience level detection with InvexPro promotion opportunities
        experience_keywords = {
            'beginner': ['new', 'beginner', 'starting', 'never', 'first time'],
            'intermediate': ['some', 'few months', 'year'],
            'advanced': ['experienced', 'professional', 'years']
        }
        
        for level, keywords in experience_keywords.items():
            if any(word in message_lower for word in keywords):
                updates['experience_level'] = level
                updates['conversation_stage'] = 'experience_set'
                
                # Suggest InvexPro for intermediate and advanced users
                if level in ['intermediate', 'advanced']:
                    updates['should_promote_invexpro'] = True
                    updates['promotion_context'] = 'take your business to the next level'
        
        return updates

    def update_achievements(self, user_id: str, context: dict) -> list:
        """Update and return new achievements for the user."""
        user_achievements = context.get('achievements', [])
        new_achievements = []
        
        # Check for new achievements
        if not user_achievements:
            new_achievements.append(self.achievements['first_chat'])
        
        if context.get('budget') and 'budget_set' not in user_achievements:
            new_achievements.append(self.achievements['budget_set'])
        
        if context.get('sales_count', 0) >= 1 and 'first_sale' not in user_achievements:
            new_achievements.append(self.achievements['first_sale'])
        
        if context.get('avg_response_time', 3600) < 1800 and 'quick_response' not in user_achievements:
            new_achievements.append(self.achievements['quick_response'])
        
        if context.get('bulk_purchases', 0) >= 5 and 'bulk_buyer' not in user_achievements:
            new_achievements.append(self.achievements['bulk_buyer'])
        
        if context.get('total_profit', 0) >= 500 and 'profit_maker' not in user_achievements:
            new_achievements.append(self.achievements['profit_maker'])
        
        if context.get('positive_feedback', 0) >= 10 and 'feedback_king' not in user_achievements:
            new_achievements.append(self.achievements['feedback_king'])
        
        # Update user's achievements
        context['achievements'] = list(set(user_achievements + new_achievements))
        self.update_context(user_id, context)
        
        return new_achievements
    
    def get_progress_summary(self, user_id: str) -> str:
        """Get a summary of user's progress and achievements."""
        context = self.get_user_context(user_id)
        achievements = context.get('achievements', [])
        
        summary = [" Your Progress:"]
        
        # Add stats
        stats = {
            'Sales': context.get('sales_count', 0),
            'Total Profit': f"${context.get('total_profit', 0):,.2f}",
            'Positive Feedback': context.get('positive_feedback', 0),
            'Response Rate': f"{context.get('response_rate', 0)}%"
        }
        
        for key, value in stats.items():
            summary.append(f"• {key}: {value}")
        
        # Add achievements
        if achievements:
            summary.append("\n Your Achievements:")
            for achievement in achievements:
                summary.append(f"• {achievement}")
        
        # Add next goals
        next_goals = self.get_next_goals(context)
        if next_goals:
            summary.append("\n Next Goals:")
            for goal in next_goals:
                summary.append(f"• {goal}")
        
        return "\n".join(summary)
    
    def get_next_goals(self, context: dict) -> list:
        """Get next goals based on user's progress."""
        goals = []
        
        if context.get('sales_count', 0) < 1:
            goals.append("Make your first sale")
        elif context.get('sales_count', 0) < 5:
            goals.append("Reach 5 sales")
        
        if context.get('total_profit', 0) < 500:
            goals.append(f"Reach $500 in profit (Currently: ${context.get('total_profit', 0):,.2f})")
        
        if context.get('positive_feedback', 0) < 10:
            goals.append(f"Get {10 - context.get('positive_feedback', 0)} more positive feedback")
        
        return goals

    def reset_user_context(self, user_id: str):
        """Reset a user's context to initial state."""
        self.conversations[user_id] = {
            'last_updated': datetime.now(),
            'context': {
                'budget': None,
                'interests': [],
                'experience_level': None,
                'previous_purchases': [],
                'conversation_stage': 'initial',
                'last_topic': None,
                'should_promote_invexpro': False,
                'promotion_context': '',
                'achievements': [],
                'sales_count': 0,
                'total_profit': 0,
                'positive_feedback': 0,
                'avg_response_time': 3600,
                'bulk_purchases': 0,
                'response_rate': 0,
                'sales_history': [],
                'feedback_history': []
            },
            'history': []
        }
