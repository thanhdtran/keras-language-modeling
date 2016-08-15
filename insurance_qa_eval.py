from __future__ import print_function

import os

import sys
import random
from time import strftime, gmtime

import pickle
import json

from keras.optimizers import SGD
from scipy.stats import rankdata

random.seed(42)


class Evaluator:
    def __init__(self, conf, model=None, optimizer=None):
        try:
            data_path = os.environ['INSURANCE_QA']
        except KeyError:
            print("INSURANCE_QA is not set. Set it to your clone of https://github.com/codekansas/insurance_qa_python")
            sys.exit(1)
        if isinstance(conf, str):
            conf = json.load(open(conf, 'rb'))
        self.model = conf['model'](conf) if model is None else model
        self.path = data_path
        self.conf = conf
        self.params = conf['training']
        optimizer = self.params['optimizer'] if optimizer is None else optimizer
        self.model.compile(optimizer)
        self.answers = self.load('answers') # self.load('generated')
        self._vocab = None
        self._reverse_vocab = None
        self._eval_sets = None

    ##### Resources #####

    def load(self, name):
        return pickle.load(open(os.path.join(self.path, name), 'rb'))

    def vocab(self):
        if self._vocab is None:
            self._vocab = self.load('vocabulary')
        return self._vocab

    def reverse_vocab(self):
        if self._reverse_vocab is None:
            vocab = self.vocab()
            self._reverse_vocab = dict((v.lower(), k) for k, v in vocab.items())
        return self._reverse_vocab

    ##### Loading / saving #####

    def save_epoch(self, epoch):
        if not os.path.exists('models/'):
            os.makedirs('models/')
        self.model.save_weights('models/weights_epoch_%d.h5' % epoch, overwrite=True)

    def load_epoch(self, epoch):
        assert os.path.exists('models/weights_epoch_%d.h5' % epoch), 'Weights at epoch %d not found' % epoch
        self.model.load_weights('models/weights_epoch_%d.h5' % epoch)

    ##### Converting / reverting #####

    def convert(self, words):
        rvocab = self.reverse_vocab()
        if type(words) == str:
            words = words.strip().lower().split(' ')
        return [rvocab.get(w, 0) for w in words]

    def revert(self, indices):
        vocab = self.vocab()
        return [vocab.get(i, 'X') for i in indices]

    ##### Padding #####

    def padq(self, data):
        return self.pad(data, self.conf.get('question_len', None))

    def pada(self, data):
        return self.pad(data, self.conf.get('answer_len', None))

    def pad(self, data, len=None):
        from keras.preprocessing.sequence import pad_sequences
        return pad_sequences(data, maxlen=len, padding='post', truncating='post', value=0)

    ##### Training #####

    def print_time(self):
        print(strftime('%Y-%m-%d %H:%M:%S :: ', gmtime()), end='')

    def train(self):
        batch_size = self.params['batch_size']
        nb_epoch = self.params['nb_epoch']
        validation_split = self.params['validation_split']

        training_set = self.load('train')
        top_50 = self.load('top_50')

        questions = list()
        good_answers = list()
        indices = list()

        for j, q in enumerate(training_set):
            questions += [q['question']] * len(q['answers'])
            good_answers += [self.answers[i] for i in q['answers']]
            indices += [j] * len(q['answers'])

        questions = self.padq(questions)
        good_answers = self.pada(good_answers)

        val_loss = {'loss': 1., 'epoch': 0}

        def get_bad_samples(indices, top_50):
            return [self.answers[random.choice(top_50[i])] for i in indices]

        for i in range(1, nb_epoch):
            # sample from all answers to get bad answers
            # if i % 2 == 0:
            #     bad_answers = self.pada(random.sample(self.answers.values(), len(good_answers)))
            # else:
            #     bad_answers = self.pada(get_bad_samples(indices, top_50))
            bad_answers = self.pada(random.sample(self.answers.values(), len(good_answers)))

            print('Epoch %d :: ' % i, end='')
            self.print_time()
            hist = self.model.fit([questions, good_answers, bad_answers], nb_epoch=1, batch_size=batch_size,
                             validation_split=validation_split)

            if hist.history['val_loss'][0] < val_loss['loss']:
                val_loss = {'loss': hist.history['val_loss'][0], 'epoch': i}
            print('Best: Loss = {}, Epoch = {}'.format(val_loss['loss'], val_loss['epoch']))

            self.save_epoch(i)

        return val_loss

    ##### Evaluation #####

    def prog_bar(self, so_far, total, n_bars=20):
        n_complete = int(so_far * n_bars / total)
        if n_complete >= n_bars - 1:
            print('\r[' + '=' * n_bars + ']', end='')
        else:
            s = '\r[' + '=' * (n_complete - 1) + '>' + '.' * (n_bars - n_complete) + ']'
            print(s, end='')

    def eval_sets(self):
        if self._eval_sets is None:
            self._eval_sets = dict([(s, self.load(s)) for s in ['dev', 'test1', 'test2']])
        return self._eval_sets

    def get_score(self, verbose=False):
        for name, data in self.eval_sets().items():
            self.print_time()
            print('----- %s -----' % name)

            random.shuffle(data)

            if 'n_eval' in self.params:
                data = data[:self.params['n_eval']]

            c_1, c_2 = 0, 0

            for i, d in enumerate(data):
                self.prog_bar(i, len(data))

                indices = d['good'] + d['bad']
                answers = self.pada([self.answers[i] for i in indices])
                question = self.padq([d['question']] * len(indices))

                sims = self.model.predict([question, answers])

                n_good = len(d['good'])
                max_r = np.argmax(sims)
                max_n = np.argmax(sims[:n_good])

                r = rankdata(sims, method='max')

                if verbose:
                    min_r = np.argmin(sims)
                    amin_r = self.answers[indices[min_r]]
                    amax_r = self.answers[indices[max_r]]
                    amax_n = self.answers[indices[max_n]]

                    print(' '.join(self.revert(d['question'])))
                    print('Predicted: ({}) '.format(sims[max_r]) + ' '.join(self.revert(amax_r)))
                    print('Expected: ({}) Rank = {} '.format(sims[max_n], r[max_n]) + ' '.join(self.revert(amax_n)))
                    print('Worst: ({})'.format(sims[min_r]) + ' '.join(self.revert(amin_r)))

                c_1 += 1 if max_r == max_n else 0
                c_2 += 1 / float(r[max_r] - r[max_n] + 1)

            top1 = c_1 / float(len(data))
            mrr = c_2 / float(len(data))

            del data
            print('Top-1 Precision: %f' % top1)
            print('MRR: %f' % mrr)


if __name__ == '__main__':
    import numpy as np

    conf = {
        'n_words': 22353,
        'question_len': 150,
        'answer_len': 150,
        'margin': 0.05,
        'initial_embed_weights': 'word2vec_100_dim.embeddings',

        'training': {
            'batch_size': 100,
            'nb_epoch': 2000,
            'validation_split': 0.1,
        },

        'similarity': {
            'mode': 'gesd',
            'gamma': 1,
            'c': 1,
            'd': 2,
        }
    }

    from keras_models import ConvolutionModel
    evaluator = Evaluator(conf, model=ConvolutionModel, optimizer=SGD(lr=0.001))

    # train the model
    best_loss = evaluator.train()

    # evaluate mrr for a particular epoch
    evaluator.load_epoch(best_loss['epoch'])
    evaluator.get_score(verbose=False)
