#!/usr/bin/env python
import random
import numpy as np
import vizdoom as vd
import scipy.ndimage as Simg
import ennemies
import map_parser
from tqdm import tqdm  # NOQA

from network import tf, DRQN
from memory import ReplayMemory
from config import *  # NOQA


# Image input size
im_w, im_h = 108, 60
# Number of game features
k = 3

# Import from config
batch_size = BATCH_SIZE
sequence_length = SEQUENCE_LENGTH
n_actions = N_ACTIONS
ACTION_SET = np.eye(n_actions, dtype=np.uint32).tolist()


def create_game():
    game = vd.DoomGame()
    game.load_config("basic.cfg")

    # Ennemy detection
    walls = map_parser.parse("maps/deathmatch.txt")
    game.clear_available_game_variables()
    game.add_available_game_variable(vd.GameVariable.POSITION_X)
    game.add_available_game_variable(vd.GameVariable.POSITION_Y)
    game.add_available_game_variable(vd.GameVariable.POSITION_Z)
    game.set_labels_buffer_enabled(True)

    game.init()
    return game, walls


def play_episode(game, walls, verbose=False):
    epsilon = 1
    game.new_episode()
    dump = []
    zoomed = np.zeros((500, im_h, im_w, 3), dtype=np.uint8)
    while not game.is_episode_finished():
        # Get screen buf
        state = game.get_state()
        S = state.screen_buffer # NOQA

        # Resample to our network size
        h, w = S.shape[:2]
        Simg.zoom(S, [1.*im_h/h, 1.*im_w/w, 1], output=zoomed[len(dump)], order=0)
        S = zoomed[len(dump)]

        game_features = ennemies.has_visible_entities(state, walls)

        # Epsilon-Greedy strat
        if np.random.rand() < epsilon:
            action = random.choice(ACTION_SET)
        else:
            action_no = sess.run(main.choice, feed_dict={
                main.images: [[S]],
            })
            action = ACTION_SET[action_no[0][0]]

        if verbose:
            print(action)
        reward = game.make_action(action)
        dump.append((S, action, reward, game_features))
    return dump


def wrap_play_episode(i):
    game, walls = create_game()
    res = play_episode(game, walls)
    game.close()
    return res


if __name__ == '__main__':
    print('Building main DRQN')
    main = DRQN(im_h, im_w, k, n_actions, 'main', learning_rate=LEARNING_RATE)
    # print('Building target DRQN')
    # target = DRQN(im_h, im_w, k, n_actions, 'target')
    # TODO target = main

    # initial LSTM state
    state = (np.zeros([batch_size, main.h_size]),
             np.zeros([batch_size, main.h_size]))

    with tf.Session() as sess:
        init = tf.global_variables_initializer()
        sess.run(init)
        print("Training vars:", [v.name for v in tf.trainable_variables()])

        mem = ReplayMemory(min_size=MIN_MEM_SIZE, max_size=MAX_MEM_SIZE)

        from multiprocessing import Pool, cpu_count
        cores = min(cpu_count(), MAX_CPUS)
        workers = Pool(cores)

        # 1 / Bootstrap memory
        print("--------")
        print("mem_size")
        while not mem.initialized:
            for episode in workers.map(wrap_play_episode, range(cores)):
                mem.add(episode)
            print(len(mem))

        # 2 / Replay and learn
        print("--------")
        print("training_step,loss")
        for i in range(TRAINING_STEPS):
            # Play and add new episodes to memory
            for episode in workers.map(wrap_play_episode, range(cores)):
                mem.add(episode)

            for j in range(10):
                # Sample a batch and ingest into the NN
                samples = mem.sample(batch_size*cores, sequence_length)
                # screens, actions, rewards, game_features
                S, A, R, F = map(np.array, zip(*samples))
                main.learn_game_features(S, F)

            loss = main.current_game_features_loss(S, F)
            print("{},{}".format(i, loss))
