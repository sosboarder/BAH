import os
import requests
from flask import Flask, request, render_template, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet
from threading import Thread
import json
import random


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_POOL_SIZE"] = 15
db = SQLAlchemy(app)
socketio = SocketIO(app)

PREFIX = "CAH "


class Player:
    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name
        self.hand = []
        self.won = []

    def pick_up_white(self, card):
        self.hand.append(card)

    def score(self, card):
        self.won.append(card)

    """
    def discard_all(self):
        hand = self.hand
        self.hand = None
        return hand
    """


class Game:
    def __init__(self, group_id):
        self.group_id = group_id
        self.players = {}
        self.selection = []
        self.hand_size = 8
        self.build_decks()
        self.czar_user_id = None
        self.choose_black_card()

    def build_decks(self):
        self.build_black_deck()
        self.build_white_deck()

    def build_black_deck(self):
        with open("resources/cah/black.json", "r") as f:
            self.black = json.load(f)
        # Filter out Pick 2 cards for now
        self.black = [card for card in self.black if card.count("_") == 1]
        self.black = [card.replace("_", "_" * 5) for card in self.black]
        random.shuffle(self.black)

    def build_white_deck(self):
        with open("resources/cah/white.json", "r") as f:
            self.white = json.load(f)
        random.shuffle(self.white)

    def choose_black_card(self):
        self.current_black_card = self.black.pop()

    def assign_czar(self, user_id=None):
        if user_id is None:
            user_id = random.choice(list(self.players.keys()))
        # TODO: Do we need this function?
        # At least make it more pythonic
        self.czar_user_id = user_id

    def join(self, user_id, name):
        if user_id in self.players:
            return False
        self.players[user_id] = Player(user_id, name)
        self.deal(user_id)
        if self.czar_user_id is None:
            self.assign_czar(user_id)

    def deal(self, user_id):
        for i in range(self.hand_size):
            self.players[user_id].pick_up_white(self.white.pop())

    def has_played(self, user_id):
        """
        Check whether a user has played a card already this round.
        """
        for candidate_id, card in self.selection:
            if candidate_id == user_id:
                return True
        return False

    def player_choose(self, user_id, card_index):
        if self.has_played(user_id):
            return False
        card = self.players[user_id].hand.pop(card_index)
        self.selection.append((user_id, card))
        # TODO: this is repeated from above, make a method to draw cards
        self.players[user_id].pick_up_white(self.white.pop())
        return True

    def players_needed(self):
        return len(self.players) - len(self.selection) - 1

    def is_czar(self, user_id):
        return self.czar_user_id == user_id

    def get_nth_card_user_id(self, n):
        # TODO: this relies on dictionaries staying in a static order, which they do NOT necessarily!
        # Use a less lazy implementation.
        counter = 0
        for user_id, card in self.selection:
            if counter == n:
                return user_id, card
            counter += 1

    def czar_choose(self, card_index):
        user_id, card = self.get_nth_card_user_id(card_index)
        self.players[user_id].score(self.current_black_card)
        self.choose_black_card()
        self.selection = []
        self.assign_czar(user_id)
        # Return card and winner
        return card, self.players[user_id]

    """
    def discard(self, user_id):
        if user_id not in self.players:
            return False
        self.white = self.players[user_id].discard_all() + self.white
        self.deal(user_id)
    """


class CardsAgainstHumanity(Module):
    DESCRIPTION = "Play everyone's favorite card game for terrible people. Commands: start, end"
    ARGC = 1

    def __init__(self):
        super().__init__()
        self.games = {}
        # TODO: use references to Player objects??
        self.playing = {}

    def add_player(self, group_id, user_id, name):
        # This is a function so that it can be called automatically when a user is joining or when they're starting a game
        self.playing[user_id] = group_id
        self.games[group_id].join(user_id, name)

    def response(self, query, message):
        # TODO: fix this mess
        arguments = query.split()
        command = arguments.pop(0)
        group_id = message.group_id
        user_id = message.user_id
        name = message.name
        if command == "start":
            if group_id in self.games:
                return "Game already started!"
            self.games[group_id] = Game(group_id)
            self.add_player(group_id, user_id, name)
            return (f"Cards Against Humanity game started. {name} added to game as first Czar. Play at https://yalebot.herokuapp.com/cah/join.\n"
                    "Other players can say !cah join to join. !cah end will terminate the game.\n")
        elif command == "end":
            if group_id not in self.games:
                return "No game in progress."
            game = self.games.pop(group_id)
            for user_id in game.players:
                self.playing.pop(user_id)
            return "Game ended. Say !cah start to start a new game."
        elif command == "join":
            if user_id in self.playing:
                return "You're already in a game."
            if group_id not in self.games:
                return "No game in progress. Say !cah start to start a game."
            self.add_player(group_id, user_id, name)
            return f"{name} has joined the game! Please go to https://yalebot.herokuapp.com/cah/join to play."
        elif command == "leave":
            if user_id in self.playing:
                self.playing.pop(user_id)
                return f"Removed {name} from the game."
            else:
                return f"{name} is not currently in a game."
        elif command == "info":
            return str(self.games) + " " + str(self.playing) + " " + str(self)
        """
        elif command == "refresh":
            self.games[group_id].refresh(user_id)
        """

    def get_user_game(self, user_id):
        game_group_id = self.playing.get(user_id)
        if game_group_id is None:
            return None
        return self.games[game_group_id]

def process_message(message):
    responses = []
    forename = message.name.split(" ", 1)[0]
    if message["sender_type"] == "user":
        if message.text.startswith(PREFIX):
            instructions = message.text[len(PREFIX):].strip().split(None, 1)
            command = instructions.pop(0).lower()
            query = instructions[0] if len(instructions) > 0 else ""
            """
            if command == "help":
                if query:
                    query = query.strip(PREFIX)
                    elif query in commands:
                        responses.append(PREFIX + query + ": " + commands[query].DESCRIPTION + f". Requires {commands[query].ARGC} argument(s).")
                    else:
                        responses.append("No such command.")
                else:
                    help_string = "--- Help ---"
                    help_string += "\nStatic commands: " + ", ".join([PREFIX + title for title in static_commands])
                    help_string += "\nTools: " + ", ".join([PREFIX + title for title in commands])
                    help_string += f"\n(Run `{PREFIX}help commandname` for in-depth explanations.)"
                    responses.append(help_string)
            """
    return responses


def reply(message, group_id):
    """
    Calculate message response, then send any response to the group it came from.
    Designed to be run in a thread.

    :param message: dictionary of message data received from webhook.
    :param group_id: ID of group in which message was sent.
    """
    send(process_message(message), group_id)


@app.route("/message", methods=["POST"])
def receive_message_callback():
    """
    Receive callback to URL when message is sent in the group.
    """
    # Retrieve data on that GroupMe message.
    message = request.get_json()
    group_id = message["group_id"]
    # Begin reply process in a new thread.
    Thread(target=reply, args=(message, group_id)).start()
    return "ok", 200


def send(message, group_id):
    """
    Reply in chat.
    :param message: text of message to send.
    :param group_id: ID of group in which to send message.
    """
    if message:
        bot = Bot.query.get(group_id)
        # Close session so it won't remain locked on database
        db.session.close()
        data = {
            "bot_id": bot.bot_id,
            "text": message,
        }
        response = requests.post("https://api.groupme.com/v3/bots/post", data=data)


@app.route("/")
def home():
    return render_template("index.html")


def in_group(group_id):
    return db.session.query(db.exists().where(Bot.group_id == group_id)).scalar()


@app.route("/manager", methods=["GET", "POST"])
def manager():
    access_token = request.args["access_token"]
    if request.method == "POST":
        # Build and send bot data
        group_id = request.form["group_id"]
        bot = {
            "name": "Bot Against Humanity",
            "group_id": group_id,
            "avatar_url": "https://i.groupme.com/200x200.png.092e3648ee2745aeb3296a51b3a85e0f",
            "callback_url": "https://botagainsthumanitygroupme.herokuapp.com/",
        }
        me = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
        result = requests.post(f"https://api.groupme.com/v3/bots?token={access_token}",
                               json={"bot": bot}).json()["response"]["bot"]
        group = requests.get(f"https://api.groupme.com/v3/groups/{group_id}?token={access_token}").json()["response"]

        # Store in database
        registrant = Bot(group_id, group["name"], result["bot_id"], me["user_id"], me["name"], access_token)
        db.session.add(registrant)
        db.session.commit()
    groups = requests.get(f"https://api.groupme.com/v3/groups?token={access_token}").json()["response"]
    groups = [group for group in groups if not Bot.query.get(group["group_id"])]
    bots = requests.get(f"https://api.groupme.com/v3/bots?token={access_token}").json()["response"]
    bots = [bot for bot in bots if Bot.query.get(bot["group_id"])]
    return render_template("manager.html", access_token=access_token, groups=groups, bots=bots)


class Bot(db.Model):
    __tablename__ = "bots"
    group_id = db.Column(db.String(16), unique=True, primary_key=True)
    group_name = db.Column(db.String(50))
    bot_id = db.Column(db.String(26), unique=True)
    owner_id = db.Column(db.String(16))
    owner_name = db.Column(db.String(64))
    access_token = db.Column(db.String(32))

    def __init__(self, group_id, group_name, bot_id, owner_id, owner_name, access_token):
        self.group_id = group_id
        self.group_name = group_name
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.access_token = access_token


@app.route("/delete", methods=["POST"])
def delete_bot():
    data = request.get_json()
    access_token = data["access_token"]
    bot = Bot.query.get(data["group_id"])
    req = requests.post(f"https://api.groupme.com/v3/bots/destroy?token={access_token}", json={"bot_id": bot.bot_id})
    if req.ok:
        db.session.delete(bot)
        db.session.commit()
        return "ok", 200


@app.route("/cah", methods=["GET"])
def cah():
    access_token = request.args["access_token"]
    return render_template("cah.html")


@app.route("/cah/join")
def cah_join_redirect():
    return redirect("https://oauth.groupme.com/oauth/authorize?client_id=iEs9DrSihBnH0JbOGZSWK8SdsqRt0pUn8EpulL8Fia3rf6QM", code=302)


@socketio.on("cah_connect")
def cah_connect(data):
    access_token = data["access_token"]
    # TODO: DRY!!
    user = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
    user_id = user["user_id"]
    game = commands["cah"].get_user_game(user_id)

    joined = cah_ping(access_token, room=False)
    if joined:
        join_room(game.group_id)
        cah_ping(access_token, single=False)


def cah_ping(access_token, room=True, single=True):
    # TODO: These lines are repeated like three times what are you DOING
    # TODO: Clean this up in the morning when you're sane
    user = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
    user_id = user["user_id"]
    game = commands["cah"].get_user_game(user_id)
    if room:
        selection = [card for _, card in game.selection]
        emit("cah_ping", {"black_card": game.current_black_card,
                          "selection_length": len(selection),
                          "selection": selection if game.players_needed() == 0 else None},
             room=game.group_id)
    if single:
        if game is None:
            emit("cah_update_user", {"joined": False})
            return False
        player = game.players[user_id]
        is_czar = game.is_czar(user_id)
        emit("cah_update_user", {"joined": True,
                                 "is_czar": is_czar,
                                 "hand": player.hand,
                                 "score": len(player.won)})
        return True


@socketio.on("cah_selection")
def cah_selection(data):
    access_token = data["access_token"]
    user = requests.get(f"https://api.groupme.com/v3/users/me?token={access_token}").json()["response"]
    user_id = user["user_id"]
    game = commands["cah"].get_user_game(user_id)
    player = game.players[user_id]
    group_id = game.group_id
    if game.is_czar(user_id):
        card, player = game.czar_choose(data["card_index"])
        send("The Card Czar has selected \"{card}\" played by {name}, who now has a score of {score}.".format(card=card,
                                                                                                              name=player.name,
                                                                                                              score=len(player.won)), group_id)
        send("The next black card is \"{card}\" and {name} is now Czar.".format(card=game.current_black_card,
                                                                                name=player.name), group_id)
    else:
        permitted = game.player_choose(user_id, data["card_index"])
        remaining_players = game.players_needed()
        if permitted:
            send(f"{player.name} has played a card. {remaining_players} still need to play.", group_id)
    cah_ping(access_token)
