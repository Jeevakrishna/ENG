
import tkinter as tk
import random

# Window settings
WIDTH = 400
HEIGHT = 600

root = tk.Tk()
root.title("Flappy Bird")
root.resizable(False, False)

canvas = tk.Canvas(
    root,
    width=WIDTH,
    height=HEIGHT,
    bg="skyblue"
)
canvas.pack()

# Game variables
bird_x = 80
bird_y = 250
bird_velocity = 0
gravity = 0.5
jump_strength = -8

pipe_width = 60
pipe_gap = 170
pipe_speed = 6

pipes = []
score = 0
game_over = False
started = False

# Draw background
def draw_background():
    canvas.create_rectangle(
        0, 550, WIDTH, HEIGHT,
        fill="lightgreen",
        outline=""
    )

# Draw bird
def draw_bird():
    canvas.create_oval(
        bird_x - 15,
        bird_y - 15,
        bird_x + 15,
        bird_y + 15,
        fill="yellow",
        outline="orange",
        width=2,
        tags="bird"
    )

    canvas.create_oval(
        bird_x + 5,
        bird_y - 8,
        bird_x + 10,
        bird_y - 3,
        fill="black",
        tags="bird"
    )

# Create pipes
def create_pipe():
    gap_y = random.randint(180, 400)

    pipes.append({
        "x": WIDTH,
        "gap_y": gap_y,
        "scored": False
    })

# Draw pipes
def draw_pipes():
    canvas.delete("pipes")

    for pipe in pipes:
        x = pipe["x"]
        gap_y = pipe["gap_y"]

        canvas.create_rectangle(
            x, 0,
            x + pipe_width, gap_y - pipe_gap // 2,
            fill="green",
            outline="darkgreen",
            width=3,
            tags="pipes"
        )

        canvas.create_rectangle(
            x, gap_y + pipe_gap // 2,
            x + pipe_width, 550,
            fill="green",
            outline="darkgreen",
            width=3,
            tags="pipes"
        )

# Jump bird
def flap(event=None):
    global bird_velocity, started

    if game_over:
        return

    started = True
    bird_velocity = jump_strength

# Keyboard controls
def key_press(event):
    global game_over

    if event.keysym == "space":
        flap()

    elif event.keysym.lower() == "r" and game_over:
        restart()

# Collision detection
def check_collision():
    global game_over

    if bird_y > 535 or bird_y < 0:
        game_over = True
        return

    for pipe in pipes:
        x = pipe["x"]
        gap_y = pipe["gap_y"]

        bird_left = bird_x - 12
        bird_right = bird_x + 12
        bird_top = bird_y - 12
        bird_bottom = bird_y + 12

        if bird_right > x and bird_left < x + pipe_width:
            if (bird_top < gap_y - pipe_gap // 2 or
                bird_bottom > gap_y + pipe_gap // 2):
                game_over = True

# Restart game
def restart():
    global bird_y, bird_velocity, pipes
    global score, game_over, started

    bird_y = 250
    bird_velocity = 0
    pipes = []
    score = 0
    game_over = False
    started = False

    canvas.delete("all")
    draw_background()
    draw_bird()

# Main game loop
def update():
    global bird_y, bird_velocity, score

    canvas.delete("bird")
    canvas.delete("score")
    canvas.delete("message")

    if started and not game_over:

        # Gravity
        bird_velocity += gravity
        bird_y += bird_velocity

        # Move pipes
        for pipe in pipes:
            pipe["x"] -= pipe_speed

            # Score
            if not pipe["scored"] and pipe["x"] + pipe_width < bird_x:
                score += 1
                pipe["scored"] = True

        # Remove old pipes
        pipes[:] = [p for p in pipes if p["x"] > -pipe_width]

        # Create new pipes
        if len(pipes) == 0 or pipes[-1]["x"] < 220:
            create_pipe()

        check_collision()

    draw_pipes()
    draw_bird()

    canvas.create_text(
        200, 40,
        text=f"Score: {score}",
        font=("Arial", 24, "bold"),
        fill="white",
        tags="score"
    )

    if not started and not game_over:
        canvas.create_text(
            200, 300,
            text="PRESS SPACE TO START",
            font=("Arial", 18, "bold"),
            fill="white",
            tags="message"
        )

    if game_over:
        canvas.create_text(
            200, 260,
            text="GAME OVER",
            font=("Arial", 30, "bold"),
            fill="red",
            tags="message"
        )

        canvas.create_text(
            200, 310,
            text="Press R to Restart",
            font=("Arial", 17),
            fill="white",
            tags="message"
        )

    root.after(20, update)

# Start game
draw_background()
draw_bird()

root.bind("<KeyPress>", key_press)

root.after(20, update)

root.mainloop()
