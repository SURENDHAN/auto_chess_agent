import os
import threading
import chess
import berserk
import random
from dotenv import load_dotenv

# --- 1. SETUP & AUTHENTICATION ---
load_dotenv()

# Get Tokens
LICHESS_TOKEN = os.getenv("lichess")

# Validation
if not LICHESS_TOKEN:
    raise ValueError("❌ Error: 'lichess' token not found in .env file.") 

session = berserk.TokenSession(LICHESS_TOKEN)
client = berserk.Client(session=session)

import platform
import time
import requests

# --- 2. THE REASONING BRAIN (Stockfish) ---
from stockfish import Stockfish

# Telegram Settings
TELEGRAM_TOKEN = os.getenv("telegram_token")
TELEGRAM_CHAT_ID = os.getenv("telegram_chat_id")

def send_telegram(message):
    """Sends a notification to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram Failed: {e}")

# Determine Stockfish path based on OS
system = platform.system()
if system == "Windows":
    STOCKFISH_PATH = os.path.join(os.path.dirname(__file__), "stockfish", "stockfish-windows-x86-64-avx2.exe")
else:
    # Linux (PythonAnywhere / Railway)
    # 1. Check local folder first (common for PythonAnywhere)
    local_binary = os.path.join(os.path.dirname(__file__), "stockfish")
    if os.path.exists(local_binary):
        STOCKFISH_PATH = local_binary
    # 2. Check system install (common for Railway/Docker)
    elif os.path.exists("/usr/games/stockfish"):
        STOCKFISH_PATH = "/usr/games/stockfish"
    # 3. Fallback to command line
    else:
        STOCKFISH_PATH = "stockfish"

class StockfishBrain:
    """
    Uses Stockfish engine for strong chess play.
    """
    def __init__(self):
        try:
            # Using depth 22 for Grandmaster+ level
            self.engine = Stockfish(path=STOCKFISH_PATH, depth=22, parameters={
                "Threads": 2, 
                "Hash": 64, 
                "Contempt": 20, # Be aggressive, try to win
                "Minimum Thinking Time": 1000
            })
            self.engine.set_skill_level(20)
            print("✅ Super Stockfish engine loaded successfully! (Depth: 22)")
        except Exception as e:
            print(f"❌ Failed to load Stockfish: {e}")
            self.engine = None
    
    def decide_move(self, board: chess.Board):
        fen = board.fen()
        print(f"🧠 MAX POWER Stockfish Thinking... (FEN: {fen})")
        
        if self.engine is None:
            return random.choice(list(board.legal_moves))
        
        try:
            self.engine.set_fen_position(fen)
            
            # Think for 2.5 seconds or until depth is reached
            best_move_uci = self.engine.get_best_move_time(2500)
            
            if best_move_uci:
                print(f"🤖 Stockfish Determined: {best_move_uci}")
                return chess.Move.from_uci(best_move_uci)
            
            print("⚠️ Stockfish returned invalid move, playing random.")
            return random.choice(list(board.legal_moves))
            
        except Exception as e:
            print(f"❌ Stockfish Error: {e}")
            return random.choice(list(board.legal_moves))
    def get_evaluation(self, board):
        """Returns centipawn evaluation for the side to move. Positive = advantage."""
        if self.engine is None:
            return 0
        try:
            self.engine.set_fen_position(board.fen())
            eval = self.engine.get_evaluation()
            # eval is like {'type': 'cp', 'value': 35} or {'type': 'mate', 'value': -2}
            
            if eval['type'] == 'mate':
                # Mate in X. Large value.
                return 10000 if eval['value'] > 0 else -10000
            
            return eval['value']
        except:
            return 0

# --- 3. THE AGENT BODY (Lichess Connection) ---
class GameHandler(threading.Thread):
    def __init__(self, game_id, **kwargs):
        super().__init__(**kwargs)
        self.game_id = game_id
        self.board = chess.Board()
        self.stream = client.bots.stream_game_state(game_id)
        self.brain = StockfishBrain() # Connect Stockfish

    def run(self):
        print(f"🚀 Game Started! ID: {self.game_id}")
        url = f"https://lichess.org/{self.game_id}"
        print(f"👀 Watch Live: {url}")
        
        # Notify Telegram
        send_telegram(f"🚀 **Game Started!**\nPlaying vs: Unknown\n[Watch Live]({url})")

        for event in self.stream:
            if event['type'] == 'gameState':
                self.handle_state_change(event)
            elif event['type'] == 'gameFull':
                # gameFull contains 'state' nested inside, also save our color
                self.my_color = 'white' if event['white'].get('id', '').lower() == 'surendhan' else 'black'
                opponent = event['black']['username'] if self.my_color == 'white' else event['white']['username']
                print(f"♟️ Playing as: {self.my_color} vs {opponent}")
                
                # Update Telegram with opponent details
                send_telegram(f"♟️ **Playing as {self.my_color.title()}**\nVs: {opponent}\n[Watch Live]({url})")

                if 'state' in event:
                    self.handle_state_change(event['state'])
                else:
                    self.handle_state_change(event)
    
    def handle_state_change(self, state):
        # Update internal board
        self.board = chess.Board()
        moves_str = state.get('moves', '')
        
        if moves_str:
            for move in moves_str.split():
                self.board.push_uci(move)
        
        # Check Game Over
        if self.board.is_game_over():
            result = self.board.result()
            print(f"🏁 Game Over: {result}")
            send_telegram(f"🏁 **Game Over**\nResult: {result}\nMoves: {self.board.fullmove_number}")
            return
        
        # --- DRAW OFFER LOGIC ---
        # Check if opponent offered a draw
        # 'wDraw' means white is offering. 'bDraw' means black is offering.
        opponent_offering_draw = False
        if self.my_color == 'black' and state.get('wDraw'):
            opponent_offering_draw = True
        elif self.my_color == 'white' and state.get('bDraw'):
            opponent_offering_draw = True

        if opponent_offering_draw:
            print(f"🤝 Opponent offered draw!")
            # Evaluate position
            eval_cp = self.brain.get_evaluation(self.board)
            print(f"📊 Evaluation: {eval_cp} cp")
            
            # Accept if we are losing or drawn (eval < 100 cp)
            # Since eval is from side to move (us), if it's low, we are not winning much.
            if eval_cp < 100:
                print("✅ Accepting draw offer (Eval < 100cp)")
                try:
                    client.board.accept_draw(self.game_id)
                    return # Accepted, no need to move (game will end)
                except Exception as e:
                    print(f"⚠️ Failed to accept draw: {e}")
            else:
                print("❌ Declining/Ignoring draw (We are winning!)")

        # Proper turn check based on color
        is_white_turn = self.board.turn == chess.WHITE
        my_color = getattr(self, 'my_color', 'white')
        
        is_my_turn = (my_color == 'white' and is_white_turn) or (my_color == 'black' and not is_white_turn)
        
        if not is_my_turn:
            return  # Not our turn, wait
        
        if self.board.is_game_over():
            print("🏁 Game Over!")
            return

        try:
            # 1. Ask Stockfish for the move
            best_move = self.brain.decide_move(self.board)
            
            # 2. Send to Lichess
            client.bots.make_move(self.game_id, best_move.uci())
            print(f"✅ Move sent: {best_move.uci()}")
            
        except berserk.exceptions.ResponseError as e:
            # This error occurs if we try to move when it's not our turn
            print(f"⚠️ Move error: {e}")
            pass

# --- 4. MAIN LOOP ---
# --- 4. AUTO-CHALLENGER ---
class ChallengeManager(threading.Thread):
    def __init__(self, my_username):
        super().__init__()
        self.my_username = my_username
        self.daemon = True # Kill when main thread ends

    def run(self):
        print(f"🔎 Auto-Challenger: STARTED. Hunting for opponents...")
        while True:
            try:
                # 1. Check if we are busy
                ongoing_games = list(client.games.get_ongoing())
                if ongoing_games:
                    print(f"🔎 Auto-Challenger: Currently playing {len(ongoing_games)} game(s). Sleeping...")
                    time.sleep(60)
                    continue

                # 2. Find bots to challenge
                print("🔎 Auto-Challenger: Seeking opponents...")
                online_bots = list(client.bots.get_online_bots(limit=20))
                
                # Filter out ourselves and maybe really weak/strong bots if needed
                valid_targets = [
                    bot for bot in online_bots 
                    if bot['username'].lower() != self.my_username.lower()
                ]

                if not valid_targets:
                    print("🔎 Auto-Challenger: No bots found. Sleeping...")
                    time.sleep(60)
                    continue

                # 3. Challenge a random one
                target = random.choice(valid_targets)
                target_name = target['username']
                print(f"⚔️  Auto-Challenger: Challenging {target_name}...")
                
                try:
                    # 3+0 Blitz game
                    client.challenges.create(target_name, rated=True, clock_limit=180, clock_increment=0, color='random')
                except Exception as e:
                    print(f"⚠️ Challenge failed: {e}")
                
                # Wait before trying again
                time.sleep(45)

            except Exception as e:
                print(f"❌ Auto-Challenger Error: {e}")
                time.sleep(60)

# --- 5. MAIN LOOP ---
if __name__ == "__main__":
    print("🤖 Gemini Chess Agent is ONLINE...")
    
    # correct way to fetch profile
    try:
        me = client.account.get()
        print(f"Logged in as: {me['username']}")
    except Exception as e:
        print(f"⚠️ Login Warning: {e}")

    # Start Auto Challenger
    if 'me' in locals():
        challenger = ChallengeManager(me['username'])
        challenger.start()

    # Listen for challenges
    for event in client.bots.stream_incoming_events():
        if event['type'] == 'challenge':
            challenger_name = event['challenge']['challenger']['name']
            
            # Don't try to accept our own challenges (if that ever happens)
            if challenger_name.lower() == me['username'].lower():
                print(f"Skipping self-challenge event.")
                continue

            print(f"🛡️ Challenge from {challenger_name}! Accepting...")
            try:
                client.bots.accept_challenge(event['challenge']['id'])
            except Exception as e:
                print(f"Could not accept challenge: {e}")
            
        elif event['type'] == 'gameStart':
            # Start a new thread for this game
            game_thread = GameHandler(event['game']['id'])
            game_thread.start()