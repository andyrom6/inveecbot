import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timedelta
import json
import anthropic
import re
import logging
from conversation_manager import ConversationManager

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('InvexBot')

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY')
print(f"Token loaded: {'Token exists' if TOKEN else 'No token found'}")
print(f"Claude API key loaded: {'Key exists' if CLAUDE_API_KEY else 'No key found'}")

# Constants
BASIC_MEMBER_ROLE_ID = 1213449559502622721  # Role ID directly in code
MAX_AI_REQUESTS = 5  # Maximum number of AI requests per user per minute
AI_COOLDOWN = 60  # Cooldown period in seconds

# Initialize Claude client
claude = anthropic.Client(api_key=CLAUDE_API_KEY)

# Store AI request counts
ai_requests = {}

# Initialize conversation manager
conversation_manager = ConversationManager()

# Load knowledge base
def load_knowledge_base():
    """Load the knowledge base from JSON file."""
    try:
        with open('knowledge_base.json', 'r') as f:
            data = json.load(f)
            logger.info("Knowledge base loaded successfully")
            logger.info(f"Available sections: {list(data.keys())}")
            return data
    except Exception as e:
        logger.error(f"Error loading knowledge base: {str(e)}")
        return {}

knowledge_base = load_knowledge_base()

def get_relevant_context(query, user_context=None):
    """Get relevant context from knowledge base based on query and user context."""
    query = query.lower()
    relevant_sections = {}
    
    # If we have a budget, prioritize products within that range
    budget = None
    if user_context and user_context.get('budget'):
        budget = float(user_context['budget'])
    else:
        # Try to extract budget from query
        import re
        amounts = re.findall(r'\$?(\d+(?:\.\d{2})?)', query)
        if amounts:
            budget = float(amounts[0])
    
    if budget is not None:
        # Always check electronics first for low budgets
        if budget <= 50:
            if 'electronics' in knowledge_base:
                electronics = knowledge_base['electronics']
                affordable_products = []
                for product in electronics.get('products', []):
                    try:
                        price = float(product['price_range'].split('-')[0])
                        if price <= budget:
                            affordable_products.append(product)
                    except (ValueError, KeyError):
                        continue
                
                if affordable_products:
                    relevant_sections['electronics'] = {
                        'products': affordable_products,
                        'market_insights': electronics.get('market_insights', {})
                    }
    
    # Add other relevant sections based on keywords
    keyword_mappings = {
        'platform': ['best_reselling_platforms'],
        'sell': ['best_reselling_platforms', 'pricing_strategies'],
        'price': ['pricing_strategies'],
        'customer': ['customer_management'],
        'storage': ['common_questions'],
        'product': ['top_selling_products'],
        'advice': ['general_advice'],
        'budget': ['budget_recommendations']
    }
    
    for keyword, sections in keyword_mappings.items():
        if keyword in query:
            for section in sections:
                if section in knowledge_base:
                    relevant_sections[section] = knowledge_base[section]
    
    # If no specific sections found, include budget recommendations
    if not relevant_sections and budget is not None:
        if 'budget_recommendations' in knowledge_base:
            relevant_sections['budget_recommendations'] = knowledge_base['budget_recommendations']
    
    return relevant_sections

def search_knowledge_base(query):
    """Search the knowledge base for relevant information."""
    query = query.lower()
    matches = []
    
    # Define keyword mappings
    keyword_mappings = {
        "platform": ["best_reselling_platforms"],
        "sell": ["best_reselling_platforms", "pricing_strategies", "general_advice"],
        "price": ["pricing_strategies", "budget_recommendations"],
        "customer": ["customer_management"],
        "storage": ["common_questions"],
        "product": ["top_selling_products"],
        "cologne": ["general_advice_colognes"],
        "budget": ["budget_recommendations"],
        "advice": ["general_advice", "buying_premium_advice"],
    }
    
    # Find relevant sections based on keywords
    relevant_sections = set()
    for keyword, sections in keyword_mappings.items():
        if keyword in query:
            relevant_sections.update(sections)
    
    # If no specific keywords found, include all possible sections
    if not relevant_sections:
        for sections in keyword_mappings.values():
            relevant_sections.update(sections)
    
    # Search through relevant sections
    for section in knowledge_base:
        if section in relevant_sections:
            data = knowledge_base[section]
            
            # Handle nested dictionaries
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list):
                        matches.extend(value)
                    elif isinstance(value, dict):
                        for subkey, subvalue in value.items():
                            if isinstance(subvalue, list):
                                matches.extend(subvalue)
            # Handle direct lists
            elif isinstance(data, list):
                matches.extend(data)
            # Handle strings
            elif isinstance(data, str):
                matches.append(data)
    
    return matches

async def get_claude_response(query, user_id=None):
    """Get a response from Claude API."""
    try:
        # Get user context if available
        context = {}
        if user_id:
            context = conversation_manager.get_user_context(user_id)
            logger.info(f"Using context for user {user_id}: {context}")
        
        # Get relevant context from knowledge base
        context_data = get_relevant_context(query, context)
        logger.info(f"Found relevant sections: {list(context_data.keys())}")
        
        # Format context string
        context_str = format_context(context_data, context)
        
        logger.info("Final context being sent to Claude:")
        logger.info(context_str)
        
        # Create system message based on conversation stage
        stage = context.get('conversation_stage', 'initial')
        base_message = "You are a friendly reselling advisor. Keep responses short, casual, and focused on one topic at a time. Use emojis naturally. Avoid overwhelming the user with too much information at once."
        
        stage_messages = {
            'initial': base_message + " Focus on understanding their budget in a friendly way.",
            'budget_set': base_message + " Suggest specific products they can start with.",
            'interests_set': base_message + " Share quick tips about their chosen products.",
            'experience_set': base_message + " Offer relevant advice for their experience level.",
            'follow_up': base_message + " Answer their specific question clearly and concisely."
        }
        
        system_message = stage_messages.get(stage, base_message)
        
        # Create messages array
        messages = [
            {
                "role": "user",
                "content": f"""Context:
{context_str}

User Query: {query}

Remember:
1. Keep it super casual and friendly
2. One main point per message
3. Use emojis naturally
4. Short, clear responses
5. Ask one follow-up question if needed"""
            }
        ]
        
        # Add conversation history if available
        if user_id:
            history = conversation_manager.get_conversation_history(user_id)
            if history:
                messages.insert(0, {
                    "role": "user",
                    "content": "Previous messages:\n" + "\n".join([
                        f"{'Bot' if msg['is_bot'] else 'User'}: {msg['message']}"
                        for msg in history[-3:]  # Only last 3 messages for focus
                    ])
                })
        
        logger.info("Sending request to Claude API")
        
        # Get response from Claude
        response = claude.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=8192,
            temperature=0.9,
            messages=messages,
            system=system_message
        )
        
        # Process response
        if response.content:
            logger.info("Received response from Claude API")
            answer = response.content[0].text
            logger.info(f"Raw response from Claude: {answer[:200]}...")
            
            # Update conversation context based on the response
            if user_id:
                conversation_manager.add_to_history(user_id, query, is_bot=False)
                conversation_manager.add_to_history(user_id, answer, is_bot=True)
                
                # Analyze user message for context updates
                updates = conversation_manager.analyze_message(user_id, query)
                if updates:
                    conversation_manager.update_context(user_id, updates)
                
                # Get follow-up question if needed
                if not any(char in answer[-1] for char in '?!.'):
                    next_question = conversation_manager.get_next_question(user_id)
                    if next_question:
                        answer += f"\n\n{next_question}"
            
            # Format the response
            formatted_response = answer
            
            # Check if response is too long for Discord
            if len(formatted_response) > 1950:
                logger.warning(f"Response too long ({len(formatted_response)} chars), truncating...")
                formatted_response = formatted_response[:1900] + "..."
            
            logger.info(f"Generated response length: {len(formatted_response)}")
            return formatted_response
            
        logger.warning("No content received from Claude API")
        return "Oops! Something went wrong. Can you try asking that again? 😅"
    
    except Exception as e:
        logger.error(f"Error in get_claude_response: {str(e)}", exc_info=True)
        return "Sorry, I ran into a problem there! Let's try again? 🔄"

def get_system_message(stage):
    """Get appropriate system message based on conversation stage."""
    base_message = "You are an expert reselling business advisor. Be friendly, encouraging, and conversational while providing practical advice."
    
    stage_messages = {
        'initial': base_message + " Focus on understanding the user's budget and goals.",
        'budget_set': base_message + " Suggest specific products and strategies based on their budget.",
        'interests_set': base_message + " Provide detailed advice about their chosen product categories.",
        'experience_set': base_message + " Tailor advice to their experience level and chosen products.",
        'follow_up': base_message + " Build on previous conversation context to provide deeper insights."
    }
    
    return stage_messages.get(stage, base_message)

def format_context(context_data, user_context):
    """Format context data with user context."""
    context_str = "Knowledge Base Information:\n\n"
    
    # Add user context if available
    if user_context:
        context_str += "=== User Context ===\n"
        if user_context.get('budget'):
            context_str += f"Budget: ${user_context['budget']}\n"
        if user_context.get('interests'):
            context_str += f"Interests: {', '.join(user_context['interests'])}\n"
        if user_context.get('experience_level'):
            context_str += f"Experience Level: {user_context['experience_level']}\n"
        context_str += "\n"
    
    # Add knowledge base context
    for section, data in context_data.items():
        section_name = section.replace('_', ' ').title()
        context_str += f"=== {section_name} ===\n"
        
        if isinstance(data, dict):
            # Handle products and market insights
            if 'products' in data:
                context_str += "Products:\n"
                for product in data['products']:
                    if isinstance(product, dict):
                        # Filter products based on budget if available
                        if user_context and user_context.get('budget'):
                            price_range = product['price_range'].split('-')
                            min_price = float(price_range[0])
                            if min_price > user_context['budget']:
                                continue
                        
                        context_str += f"- {product['name']}: {product['description']}\n"
                        context_str += f"  Price Range: {product['price_range']}\n"
                        context_str += f"  Target Market: {product['target_market']}\n"
                        context_str += "  Key Selling Points:\n"
                        for point in product['selling_points']:
                            context_str += f"    • {point}\n"
                    else:
                        context_str += f"- {product}\n"
            
            # Add other context data as before...
            # [Previous format_context code...]
    
    return context_str

def format_response(response, stage):
    """Format response with appropriate styling based on conversation stage."""
    # Add emoji prefix based on stage
    stage_emojis = {
        'initial': '👋',
        'budget_set': '💰',
        'interests_set': '🎯',
        'experience_set': '📚',
        'follow_up': '💡'
    }
    
    emoji = stage_emojis.get(stage, '💬')
    
    # Format the response
    formatted = f"{emoji} "
    
    # Add any highlights or formatting
    if "AirPods" in response:
        response = response.replace("AirPods", "**AirPods**")
    if "$" in response:
        # Bold price mentions
        import re
        response = re.sub(r'\$(\d+(?:\.\d{2})?)', r'**$\1**', response)
    
    formatted += response
    return formatted

# Set up intents
intents = discord.Intents.all()  # Enable all intents

# Create bot instance with required permissions
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    default_guild_ids=[1207605096431493140]  # Add your server ID here
)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f"Role ID to assign: {BASIC_MEMBER_ROLE_ID}")
    
    # Print permissions for each guild the bot is in
    for guild in bot.guilds:
        print(f"\nGuild: {guild.name}")
        bot_member = guild.get_member(bot.user.id)
        print(f"Bot permissions in {guild.name}: {bot_member.guild_permissions}")
        print(f"Bot roles: {[r.name for r in bot_member.roles]}")
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_member_join(member):
    # Get the verify channel
    verify_channel = discord.utils.get(member.guild.channels, name='verify')
    
    if verify_channel:
        # Send verification instructions
        verify_embed = discord.Embed(
            title="🔐 Verification Process",
            description=(
                f"Hey {member.mention}! Let's get you verified and unlock access to our community!\n"
                "Follow these simple steps:"
            ),
            color=discord.Color.from_rgb(87, 242, 135)  # Nice green color
        )
        
        verify_embed.add_field(
            name="Step 1️⃣",
            value="Type `/verify` in this channel",
            inline=False
        )
        
        verify_embed.add_field(
            name="Step 2️⃣",
            value=(
                "Fill out the quick verification form with:\n"
                "• Your preferred name 📝\n"
                "• What brings you to Invex Resell 🎯\n"
                "• A fun fact about yourself ✨"
            ),
            inline=False
        )
        
        verify_embed.add_field(
            name="Step 3️⃣",
            value="Get instant access to our community! 🚀",
            inline=False
        )
        
        verify_embed.set_thumbnail(url=member.guild.icon.url if member.guild.icon else None)
        verify_embed.set_footer(text="We can't wait to meet you! 🤝")
        
        await verify_channel.send(
            content=f"Hey {member.mention}! Let's get you started! 👋",
            embed=verify_embed
        )

# Store verification attempts and cooldowns
verification_attempts = {}
verification_cooldowns = {}

class VerifyModal(discord.ui.Modal, title='Verification Form'):
    def __init__(self):
        super().__init__()
        self.nickname = discord.ui.TextInput(
            label='What should we call you?',
            placeholder='Your preferred name or nickname',
            required=True,
            max_length=32
        )
        self.goals = discord.ui.TextInput(
            label='What brings you to Invex Resell?',
            placeholder='Tell us about your goals and interests',
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1000
        )
        self.fun_fact = discord.ui.TextInput(
            label='Share a fun fact about yourself!',
            placeholder='Something interesting about you',
            required=True,
            max_length=1000
        )
        self.add_item(self.nickname)
        self.add_item(self.goals)
        self.add_item(self.fun_fact)

    async def on_submit(self, interaction: discord.Interaction):
        # Create the embedded message for introduction
        intro_embed = discord.Embed(
            title="👋 New Member Introduction",
            description=f"Everyone welcome **{interaction.user.name}** to our amazing community! 🌟\n\n" +
                       f"They're here to be part of something special. Let's make them feel at home! ✨",
            color=discord.Color.from_rgb(88, 101, 242)  # Discord Blurple color
        )
        
        # Add user info with larger profile picture
        intro_embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
        )
        intro_embed.set_thumbnail(url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        
        # Add a divider field for better visual separation
        intro_embed.add_field(
            name="━━━━━━━━━━━━━━━",
            value="",
            inline=False
        )
        
        # Add verification details with emojis and formatting
        intro_embed.add_field(
            name="💫 Preferred Name",
            value=f"```{self.nickname.value}```",
            inline=False
        )
        
        intro_embed.add_field(
            name="🎯 Goals & Aspirations",
            value=f"```{self.goals.value}```",
            inline=False
        )
        
        intro_embed.add_field(
            name="✨ Fun Fact",
            value=f"```{self.fun_fact.value}```",
            inline=False
        )
        
        # Add another divider for visual balance
        intro_embed.add_field(
            name="━━━━━━━━━━━━━━━",
            value="",
            inline=False
        )
        
        # Add join date
        member = interaction.guild.get_member(interaction.user.id)
        joined_at = int(member.joined_at.timestamp()) if member and member.joined_at else int(datetime.now().timestamp())
        intro_embed.add_field(
            name="🕒 Joined",
            value=f"<t:{joined_at}:R>",
            inline=True
        )
        
        # Add member count
        member_count = len([m for m in interaction.guild.members if not m.bot])
        intro_embed.add_field(
            name="👥 Member",
            value=f"#{member_count}",
            inline=True
        )
        
        # Add footer with server icon
        intro_embed.set_footer(
            text=f"Welcome to {interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        
        # Add timestamp
        intro_embed.timestamp = datetime.now()
        
        # Get the chat channel
        chat_channel = discord.utils.get(interaction.guild.channels, name='chat')
        
        if chat_channel:
            # Send the introduction in chat with a welcoming message
            await chat_channel.send(
                content=f"🎉 **A new adventurer has joined us!** Please welcome {interaction.user.mention} to Invex Resell! 🚀",
                embed=intro_embed
            )
            
            # Add the verified role using Role ID
            try:
                print(f"\n=== Role Assignment Debug ===")
                print(f"User to verify: {interaction.user.name} (ID: {interaction.user.id})")
                print(f"Attempting to add role ID: {BASIC_MEMBER_ROLE_ID}")
                
                # Get bot's member object in the guild
                bot_member = interaction.guild.get_member(bot.user.id)
                print(f"\nBot Status:")
                print(f"- Name: {bot_member.name}")
                print(f"- ID: {bot_member.id}")
                print(f"- Permissions: {bot_member.guild_permissions}")
                print(f"- Roles: {[r.name for r in bot_member.roles]}")
                
                # Get all roles in the guild
                print(f"\nAll Guild Roles:")
                for r in interaction.guild.roles:
                    print(f"- {r.name} (ID: {r.id}, Position: {r.position})")
                
                # Get the role
                role = interaction.guild.get_role(BASIC_MEMBER_ROLE_ID)
                print(f"\nTarget Role:")
                print(f"- Found: {'Yes' if role else 'No'}")
                if role:
                    print(f"- Name: {role.name}")
                    print(f"- ID: {role.id}")
                    print(f"- Position: {role.position}")
                    print(f"- Bot's highest role position: {bot_member.top_role.position}")
                    print(f"- Can bot manage roles? {'Yes' if bot_member.guild_permissions.manage_roles else 'No'}")
                    print(f"- Is bot's role higher? {'Yes' if bot_member.top_role.position > role.position else 'No'}")
                    
                    # Check if bot can manage roles
                    if not bot_member.guild_permissions.manage_roles:
                        print("\n❌ Error: Bot doesn't have manage_roles permission!")
                        await interaction.response.send_message(
                            "❌ I don't have permission to manage roles. Please contact an administrator.",
                            ephemeral=True
                        )
                        return
                        
                    # Check if bot's role is high enough
                    if bot_member.top_role.position <= role.position:
                        print("\n❌ Error: Bot's role is not high enough!")
                        await interaction.response.send_message(
                            "❌ My role needs to be higher than the role I'm trying to assign. Please contact an administrator.",
                            ephemeral=True
                        )
                        return
                    
                    # Try to add the role
                    print("\nAttempting to add role...")
                    await interaction.user.add_roles(role, reason="Verification complete")
                    print("✅ Successfully added role!")
                    await interaction.response.send_message(
                        "✅ Thank you for verifying! You've been given the member role.\n"
                        "Your introduction has been posted in #chat.\n"
                        "Feel free to explore the server and engage with our amazing community! 🚀",
                        ephemeral=True
                    )
                else:
                    print("\n❌ Error: Role not found!")
                    await interaction.response.send_message(
                        "❌ Could not find the member role. Please contact an administrator.",
                        ephemeral=True
                    )
            except discord.Forbidden as e:
                print(f"\n❌ Error: Forbidden - {e}")
                await interaction.response.send_message(
                    "❌ I don't have permission to assign roles. Please contact an administrator.",
                    ephemeral=True
                )
            except Exception as e:
                print(f"\n❌ Error: {type(e).__name__} - {e}")
                await interaction.response.send_message(
                    f"❌ An error occurred: {str(e)}. Please contact an administrator.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "❌ Couldn't find required channels. Please contact an administrator.",
                ephemeral=True
            )

@bot.tree.command(name="verify", description="Start the verification process")
async def verify(interaction: discord.Interaction):
    # Check if the user is already verified
    if any(role.id == BASIC_MEMBER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You are already verified!", ephemeral=True)
        return
        
    # Check if user is in cooldown
    user_id = str(interaction.user.id)
    current_time = datetime.now()
    
    if user_id in verification_cooldowns:
        cooldown_end = verification_cooldowns[user_id]
        if current_time < cooldown_end:
            time_left = cooldown_end - current_time
            await interaction.response.send_message(
                f"Please wait {time_left.seconds} seconds before trying again.",
                ephemeral=True
            )
            return
    
    # Check verification attempts
    attempts = verification_attempts.get(user_id, 0)
    if attempts >= 3:
        # Set cooldown for 5 minutes
        verification_cooldowns[user_id] = current_time + timedelta(minutes=5)
        verification_attempts[user_id] = 0
        await interaction.response.send_message(
            "You've reached the maximum verification attempts. Please try again in 5 minutes.",
            ephemeral=True
        )
        return
    
    # Increment attempts
    verification_attempts[user_id] = attempts + 1
    
    # Send the modal
    await interaction.response.send_modal(VerifyModal())

@bot.tree.command(name="ai", description="Ask a question about reselling colognes and fragrances")
async def ai_command(interaction: discord.Interaction, question: str):
    """Handle AI command for reselling questions."""
    
    # Check rate limiting
    user_id = str(interaction.user.id)
    current_time = datetime.now()
    
    if user_id in ai_requests:
        requests, last_reset = ai_requests[user_id]
        if current_time - last_reset > timedelta(seconds=AI_COOLDOWN):
            # Reset if cooldown period has passed
            ai_requests[user_id] = (1, current_time)
        elif requests >= MAX_AI_REQUESTS:
            time_left = AI_COOLDOWN - (current_time - last_reset).seconds
            await interaction.response.send_message(
                f"You've reached the maximum number of AI requests. Please wait {time_left} seconds.",
                ephemeral=True
            )
            return
        else:
            # Increment request count
            ai_requests[user_id] = (requests + 1, last_reset)
    else:
        # First request from this user
        ai_requests[user_id] = (1, current_time)
    
    # Create thinking embed
    thinking_embed = discord.Embed(
        title="🤔 Thinking...",
        description="Let me search my knowledge base and consult with Claude...",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=thinking_embed)
    
    # Search knowledge base first
    kb_matches = search_knowledge_base(question.lower())
    
    if kb_matches:
        # Create response from knowledge base matches
        response = "\n\n".join(kb_matches[:3])  # Limit to top 3 matches
        source = "Knowledge Base"
    else:
        # If no matches found, query Claude
        response = await get_claude_response(question, user_id)
        source = "Claude AI"
    
    # Create response embed
    response_embed = discord.Embed(
        title="🎯 Reselling Advice",
        description=f"Here's what I found for: *{question}*\n\n{response}",
        color=discord.Color.green(),
        timestamp=datetime.now()
    )
    response_embed.set_footer(text=f"Source: {source} | Powered by Invex AI")
    
    # Edit the original message with the response
    await interaction.edit_original_response(embed=response_embed)

@bot.tree.command(name="help", description="Show all available commands and how to use them")
async def help_command(interaction: discord.Interaction):
    """Show all available commands and their usage."""
    try:
        await interaction.response.defer()
        
        embed = discord.Embed(
            title="📚 InvexBot Commands Guide",
            description="Here are all the commands you can use:",
            color=discord.Color.blue()
        )
        
        # Main Commands
        embed.add_field(
            name="🤝 Getting Started",
            value="`/start` - Begin your reselling journey with personalized advice\n"
                  "Example: `/start`",
            inline=False
        )
        
        # Progress Commands
        embed.add_field(
            name="📊 Track Your Progress",
            value="`/update` - Update your reselling progress\n"
                  "Options:\n"
                  "• Add Sale 📈\n"
                  "• Add Feedback ⭐\n"
                  "• Update Stats 📊\n"
                  "• Reset Progress 🔄\n"
                  "Example: `/update`\n\n"
                  "`/progress` - View your achievements and stats\n"
                  "Example: `/progress`",
            inline=False
        )
        
        # Help Commands
        embed.add_field(
            name="❓ Help & Information",
            value="`/commands` - Show this help message\n"
                  "Example: `/commands`\n\n"
                  "`/tips` - Get reselling tips based on your progress\n"
                  "Example: `/tips`",
            inline=False
        )
        
        # Add tips footer
        embed.set_footer(text="💡 Tip: Use /start to begin your reselling journey!")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in commands command: {str(e)}", exc_info=True)
        await interaction.followup.send("Oops! Something went wrong. Try again? 🔄")

@bot.tree.command(name="start", description="Start your reselling journey with personalized advice")
async def start_command(interaction: discord.Interaction, query: str = None):
    """Begin the reselling conversation with context."""
    try:
        await interaction.response.defer()
        
        if not query:
            # Default welcome message
            response = "Hey! Welcome to the reselling world! 👋\n\nThe best way to start is to figure out your budget - that way I can guide you towards the right options.\n\nHow much are you thinking of investing to get started? 💰"
        else:
            # Get response using user's ID for context
            response = await get_claude_response(query, str(interaction.user.id))
        
        # Create buttons based on context
        view = None
        context = conversation_manager.get_user_context(str(interaction.user.id))
        stage = context.get('conversation_stage', 'initial')
        
        if stage == 'budget_set':
            view = discord.ui.View(timeout=300)
            view.add_item(discord.ui.Button(label="Tell me more! 👋", custom_id="more_info", style=discord.ButtonStyle.primary))
            view.add_item(discord.ui.Button(label="How to start? 🚀", custom_id="how_to_start", style=discord.ButtonStyle.secondary))
        
        elif stage == 'interests_set':
            view = discord.ui.View(timeout=300)
            view.add_item(discord.ui.Button(label="Electronics 📱", custom_id="electronics", style=discord.ButtonStyle.primary))
            view.add_item(discord.ui.Button(label="Fashion 👕", custom_id="fashion", style=discord.ButtonStyle.secondary))
            view.add_item(discord.ui.Button(label="Luxury Items ✨", custom_id="luxury", style=discord.ButtonStyle.secondary))
        
        elif stage == 'experience_set':
            view = discord.ui.View(timeout=300)
            view.add_item(discord.ui.Button(label="First Sale Tips 🎯", custom_id="first_sale", style=discord.ButtonStyle.primary))
            view.add_item(discord.ui.Button(label="Pro Strategies 📈", custom_id="pro_tips", style=discord.ButtonStyle.secondary))
        
        # Format response with emojis and sections
        formatted_response = format_response(response, stage)
        
        # Send response with view if available
        if view:
            await interaction.followup.send(formatted_response, view=view)
        else:
            await interaction.followup.send(formatted_response)
        
    except Exception as e:
        logger.error(f"Error in start command: {str(e)}", exc_info=True)
        await interaction.followup.send("Sorry, I ran into a problem there! Let's try again? 🔄")

@bot.tree.command(name="tips", description="Get personalized reselling tips based on your progress")
async def tips_command(interaction: discord.Interaction):
    """Get personalized tips based on progress."""
    try:
        await interaction.response.defer()
        
        context = conversation_manager.get_user_context(str(interaction.user.id))
        sales_count = context.get('sales_count', 0)
        
        embed = discord.Embed(
            title="💡 Personalized Reselling Tips",
            color=discord.Color.green()
        )
        
        if sales_count == 0:
            embed.add_field(
                name="🌟 Getting Started",
                value="• Start with AirPods - only $10.90 to buy\n"
                      "• Take clear, well-lit photos\n"
                      "• Price slightly below retail\n"
                      "• List on Facebook Marketplace first\n"
                      "• Respond quickly to messages",
                inline=False
            )
        elif sales_count < 5:
            embed.add_field(
                name="📈 Growing Your Business",
                value="• Try listing on multiple platforms\n"
                      "• Build a positive feedback score\n"
                      "• Track your profits carefully\n"
                      "• Consider buying in bulk\n"
                      "• Maintain quick shipping times",
                inline=False
            )
        else:
            embed.add_field(
                name="🚀 Scaling Up",
                value="• Diversify your product range\n"
                      "• Build supplier relationships\n"
                      "• Consider using InvexPro for tracking\n"
                      "• Optimize your pricing strategy\n"
                      "• Focus on customer retention",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in tips command: {str(e)}", exc_info=True)
        await interaction.followup.send("Oops! Something went wrong. Try again? 🔄")

@bot.tree.command(name="progress", description="Check your reselling progress and achievements")
async def progress_command(interaction: discord.Interaction):
    """Show user's progress, achievements, and next goals."""
    try:
        await interaction.response.defer()
        
        # Get progress summary
        summary = conversation_manager.get_progress_summary(str(interaction.user.id))
        
        # Create embed
        embed = discord.Embed(
            title="🏆 Your Reselling Journey",
            description=summary,
            color=discord.Color.gold()
        )
        
        # Add tips based on progress
        context = conversation_manager.get_user_context(str(interaction.user.id))
        if context.get('sales_count', 0) == 0:
            embed.add_field(
                name="💡 Quick Tip",
                value="Start with AirPods - they're perfect for beginners with high profit margins!",
                inline=False
            )
        elif context.get('sales_count', 0) < 5:
            embed.add_field(
                name="💡 Pro Tip",
                value="Try listing on multiple platforms to increase your sales!",
                inline=False
            )
        
        # Add InvexPro promotion if relevant
        if context.get('sales_count', 0) >= 5 and not context.get('uses_invexpro'):
            embed.add_field(
                name="📱 Level Up Your Business",
                value="Ready to scale? Try InvexPro to manage your growing inventory!",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in progress command: {str(e)}", exc_info=True)
        await interaction.followup.send("Oops! Something went wrong. Try again? 🔄")

@bot.tree.command(name="update", description="Update your reselling progress")
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="Add Sale 📈", value="sale"),
    discord.app_commands.Choice(name="Add Feedback ⭐", value="feedback"),
    discord.app_commands.Choice(name="Update Stats 📊", value="stats"),
    discord.app_commands.Choice(name="Reset Progress 🔄", value="reset")
])
async def update_command(interaction: discord.Interaction, action: str):
    """Interactive command to update sales progress."""
    try:
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        context = conversation_manager.get_user_context(user_id)
        
        if action == "sale":
            # Create sale entry form
            modal = SaleEntryModal(user_id, title="Add New Sale 📈")
            await interaction.followup.send_modal(modal)
            
            # Wait for modal submission
            try:
                modal_interaction = await bot.wait_for(
                    "modal_submit",
                    timeout=300.0,
                    check=lambda i: i.custom_id == f"sale_modal_{user_id}"
                )
                
                # Process sale data
                item = modal_interaction.data["components"][0]["value"]
                buy_price = float(modal_interaction.data["components"][1]["value"])
                sell_price = float(modal_interaction.data["components"][2]["value"])
                platform = modal_interaction.data["components"][3]["value"]
                
                # Update stats
                profit = sell_price - buy_price
                context['sales_count'] = context.get('sales_count', 0) + 1
                context['total_profit'] = context.get('total_profit', 0) + profit
                
                # Track sale history
                if 'sales_history' not in context:
                    context['sales_history'] = []
                context['sales_history'].append({
                    'date': datetime.now().isoformat(),
                    'item': item,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'profit': profit,
                    'platform': platform
                })
                
                # Calculate success metrics
                if len(context['sales_history']) >= 2:
                    dates = [datetime.fromisoformat(s['date']) for s in context['sales_history'][-2:]]
                    days_between = (dates[1] - dates[0]).days
                    context['sales_frequency'] = f"{days_between} days between sales"
                    
                    profits = [s['profit'] for s in context['sales_history'][-5:]]
                    context['avg_profit'] = sum(profits) / len(profits)
                
                # Create success embed
                embed = discord.Embed(
                    title="🎉 Sale Added Successfully!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Item", value=item, inline=True)
                embed.add_field(name="Profit", value=f"${profit:,.2f}", inline=True)
                embed.add_field(name="Platform", value=platform, inline=True)
                
                if context['avg_profit']:
                    embed.add_field(
                        name="📊 Recent Performance",
                        value=f"Average profit: ${context['avg_profit']:,.2f}\nFrequency: {context['sales_frequency']}",
                        inline=False
                    )
                
                await modal_interaction.response.send_message(embed=embed)
                
            except asyncio.TimeoutError:
                await interaction.followup.send("Sale entry timed out. Try again with /update")
                return
                
        elif action == "feedback":
            # Create feedback form
            modal = FeedbackEntryModal(user_id, title="Add Feedback ⭐")
            await interaction.followup.send_modal(modal)
            
            try:
                modal_interaction = await bot.wait_for(
                    "modal_submit",
                    timeout=300.0,
                    check=lambda i: i.custom_id == f"feedback_modal_{user_id}"
                )
                
                # Process feedback
                rating = int(modal_interaction.data["components"][0]["value"])
                comment = modal_interaction.data["components"][1]["value"]
                
                # Update stats
                context['positive_feedback'] = context.get('positive_feedback', 0) + (1 if rating >= 4 else 0)
                
                # Track feedback history
                if 'feedback_history' not in context:
                    context['feedback_history'] = []
                context['feedback_history'].append({
                    'date': datetime.now().isoformat(),
                    'rating': rating,
                    'comment': comment
                })
                
                # Calculate feedback stats
                ratings = [f['rating'] for f in context['feedback_history']]
                avg_rating = sum(ratings) / len(ratings)
                context['avg_rating'] = avg_rating
                
                embed = discord.Embed(
                    title="⭐ Feedback Added!",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Rating", value="⭐" * rating, inline=True)
                embed.add_field(name="Average Rating", value=f"{avg_rating:.1f} ⭐", inline=True)
                
                await modal_interaction.response.send_message(embed=embed)
                
            except asyncio.TimeoutError:
                await interaction.followup.send("Feedback entry timed out. Try again with /update")
                return
                
        elif action == "stats":
            # Show detailed stats view
            sales_history = context.get('sales_history', [])
            feedback_history = context.get('feedback_history', [])
            
            embed = discord.Embed(
                title="📊 Detailed Statistics",
                color=discord.Color.blue()
            )
            
            # Sales stats
            total_sales = len(sales_history)
            if total_sales > 0:
                total_profit = sum(s['profit'] for s in sales_history)
                avg_profit = total_profit / total_sales
                best_sale = max(sales_history, key=lambda x: x['profit'])
                
                embed.add_field(
                    name="💰 Sales Performance",
                    value=f"Total Sales: {total_sales}\n"
                          f"Total Profit: ${total_profit:,.2f}\n"
                          f"Average Profit: ${avg_profit:,.2f}\n"
                          f"Best Sale: ${best_sale['profit']:,.2f} ({best_sale['item']})",
                    inline=False
                )
            
            # Feedback stats
            if feedback_history:
                avg_rating = sum(f['rating'] for f in feedback_history) / len(feedback_history)
                five_stars = sum(1 for f in feedback_history if f['rating'] == 5)
                
                embed.add_field(
                    name="⭐ Feedback Stats",
                    value=f"Average Rating: {avg_rating:.1f} ⭐\n"
                          f"5-Star Reviews: {five_stars}\n"
                          f"Total Reviews: {len(feedback_history)}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed)
            
        elif action == "reset":
            # Create confirmation button
            view = discord.ui.View(timeout=60)
            view.add_item(
                discord.ui.Button(
                    label="Confirm Reset ⚠️",
                    style=discord.ButtonStyle.danger,
                    custom_id="confirm_reset"
                )
            )
            
            await interaction.followup.send(
                "⚠️ Are you sure you want to reset all your progress? This cannot be undone!",
                view=view
            )
            
            try:
                button_interaction = await bot.wait_for(
                    "button_click",
                    timeout=60.0,
                    check=lambda i: i.custom_id == "confirm_reset" and i.user.id == interaction.user.id
                )
                
                # Reset user context
                conversation_manager.reset_user_context(user_id)
                await button_interaction.response.send_message("Progress reset successfully! Start fresh with /help")
                
            except asyncio.TimeoutError:
                await interaction.followup.send("Reset cancelled - no confirmation received")
                return
        
        # Check for new achievements
        new_achievements = conversation_manager.update_achievements(user_id, context)
        if new_achievements:
            achievement_embed = discord.Embed(
                title="🏆 New Achievements Unlocked!",
                description="\n".join(f"• {a}" for a in new_achievements),
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=achievement_embed)
        
    except Exception as e:
        logger.error(f"Error in update command: {str(e)}", exc_info=True)
        await interaction.followup.send("Oops! Something went wrong. Try again? 🔄")

class SaleEntryModal(discord.ui.Modal):
    def __init__(self, user_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_id = f"sale_modal_{user_id}"
        
        self.add_item(
            discord.ui.TextInput(
                label="Item Sold",
                placeholder="e.g. AirPods Pro",
                required=True,
                custom_id="item"
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Buy Price ($)",
                placeholder="How much did you pay?",
                required=True,
                custom_id="buy_price"
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Sell Price ($)",
                placeholder="How much did you sell it for?",
                required=True,
                custom_id="sell_price"
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Platform",
                placeholder="Where did you sell it?",
                required=True,
                custom_id="platform"
            )
        )

class FeedbackEntryModal(discord.ui.Modal):
    def __init__(self, user_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_id = f"feedback_modal_{user_id}"
        
        self.add_item(
            discord.ui.TextInput(
                label="Rating (1-5)",
                placeholder="Enter a number from 1 to 5",
                required=True,
                max_length=1,
                custom_id="rating"
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Comment (optional)",
                placeholder="Any feedback from the buyer?",
                required=False,
                style=discord.TextStyle.paragraph,
                custom_id="comment"
            )
        )

bot.run(TOKEN)