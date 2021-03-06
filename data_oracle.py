import sys
from collections import Counter

import state_machine
from utils import print_log
from amr import JAMR_CorpusReader
# from scripts.data_augment import SpecialTokens
from state_machine import Transitions

"""
    This algorithm contains heuristics for solving
    transition-based AMR parsing in a rule based way.

    Actions are
        SHIFT : move buffer[-1] to stack[-1]
        REDUCE : delete token from stack[-1]
        CONFIRM : assign a node concept
        SWAP : move stack[-2] to buffer
        LA(label) : stack[-1] parent of stack[-2]
        RA(label) : stack[-2] parent of stack[-1]
        ENTITY(type) : form a named entity
        MERGE : merge two tokens (for MWEs)
        DEPENDENT(edge,node) : Add a node which is a dependent of stack[-1]
        CLOSE : complete AMR, run post-processing
"""

use_addnode_rules = True


class AMR_Oracle:

    def __init__(self, verbose=False):
        self.amrs = []
        self.gold_amrs = []
        self.transitions = []
        self.verbose = verbose

        # predicates
        self.preds2Ints = {}
        self.possiblePredicates = {}

        self.new_edge = ''
        self.new_node = ''
        self.entity_type = ''
        self.dep_id = None

        self.swapped_words = {}

        self.possibleEntityTypes = Counter()

        self.stats = {'CONFIRM': Counter(), 'REDUCE': Counter(), 'SWAP': Counter(), 'LA': Counter(),
                      'RA': Counter(), 'ENTITY': Counter(), 'MERGE': Counter(), 'DEPENDENT': Counter(),
                      'INTRODUCE': Counter()}

    def read_actions(self, actions_file):
        transitions = []
        with open(actions_file, 'r', encoding='utf8') as f:
            sentences = f.read()
        sentences = sentences.replace('\r', '')
        sentences = sentences.split('\n\n')
        for sent in sentences:
            if not sent.strip():
                continue
            s = sent.split('\n')
            if len(s) < 2:
                raise IOError(f'Action file formatted incorrectly: {sent}')
            tokens = s[0].split('\t')
            actions = s[1].split('\t')
            transitions.append(Transitions(tokens))
            transitions[-1].applyActions(actions)
        self.transitions = transitions

    def runOracle(self, gold_amrs, action_file=None, graph_file=None, add_unaligned=0):

        print_log("oracle", "Parsing data")
        self.gold_amrs = [gold_amr.copy() for gold_amr in gold_amrs]

        start = 0
        if action_file:
            with open(action_file, 'w+') as f:
                f.write('')
        if graph_file:
            with open(graph_file, 'w+') as f:
                f.write('')

        included_unaligned = ['-', 'and', 'multi-sentence', 'person', 'cause-01', 'you', 'more', 'imperative', '1', 'thing', ]

        for sent_idx, gold_amr in enumerate(self.gold_amrs):

            if sent_idx < start:
                continue
            if self.verbose:
                print("New Sentence " + str(sent_idx) + "\n\n\n")

            tr = Transitions(gold_amr.tokens, verbose=self.verbose, add_unaligned=add_unaligned)
            self.transitions.append(tr)
            self.amrs.append(tr.amr)

            # clean alignments
            for i, tok in enumerate(gold_amr.tokens):
                align = gold_amr.alignmentsToken2Node(i+1)
                if len(align) == 2:
                    edges = [(s, r, t) for s, r, t in gold_amr.edges if s in align and t in align]
                    if not edges:
                        remove = 1
                        if gold_amr.nodes[align[1]].startswith(tok[:2]) or len(gold_amr.alignments[align[0]]) > len(gold_amr.alignments[align[1]]):
                            remove = 0
                        gold_amr.alignments[align[remove]].remove(i+1)
                        gold_amr.token2node_memo = {}

            if add_unaligned:
                for i in range(add_unaligned):
                    gold_amr.tokens.append("<unaligned>")
                    for n in gold_amr.nodes:
                        if n not in gold_amr.alignments or not gold_amr.alignments[n]:
                            if gold_amr.nodes[n] in included_unaligned:
                                gold_amr.alignments[n] = [len(gold_amr.tokens)]
                                break
            # add root
            gold_amr.tokens.append("<ROOT>")
            gold_amr.nodes[-1] = "<ROOT>"
            gold_amr.edges.append((-1, "root", gold_amr.root))
            gold_amr.alignments[-1] = [-1]

            while tr.buffer or tr.stack:

                stack0 = tr.stack[-1] if tr.stack else 'NA'
                stack1 = tr.stack[-2] if len(tr.stack) > 1 else 'NA'

                if self.tryMerge(tr, tr.amr, gold_amr):
                    tr.MERGE()
                    toks = [tr.amr.tokens[x-1] for x in tr.merged_tokens[stack0]]
                    self.stats['MERGE'][','.join(toks)] += 1

                elif self.tryEntity(tr, tr.amr, gold_amr):
                    if stack0 in tr.merged_tokens:
                        toks = [tr.amr.tokens[x-1] for x in tr.merged_tokens[stack0]]
                    else:
                        toks = [tr.amr.nodes[stack0]]
                    self.stats['ENTITY'][','.join(toks) + ' (' + self.entity_type + ')'] += 1
                    tr.ENTITY(entity_type=self.entity_type)

                elif self.tryConfirm(tr, tr.amr, gold_amr):
                    self.stats['CONFIRM'][tr.amr.nodes[stack0] + ' => ' + self.new_node] += 1
                    tr.CONFIRM(node_label=self.new_node)

                elif self.tryDependent(tr, tr.amr, gold_amr):
                    tr.DEPENDENT(edge_label=self.new_edge, node_label=self.new_node, node_id=self.dep_id)
                    self.dep_id = None
                    tok = tr.amr.nodes[stack0]
                    self.stats['DEPENDENT'][self.new_edge + ' ' + self.new_node] += 1

                elif self.tryIntroduce(tr, tr.amr, gold_amr):
                    tok1 = tr.amr.nodes[tr.latent[-1]]
                    tok2 = tr.amr.nodes[stack0]
                    self.stats['INTRODUCE'][tok1 + ' ' + tok2] += 1
                    tr.INTRODUCE()

                elif self.tryLA(tr, tr.amr, gold_amr):
                    tr.LA(edge_label=self.new_edge)
                    tok1 = tr.amr.nodes[stack0]
                    tok2 = tr.amr.nodes[stack1]
                    self.stats['LA'][tok1 + ' ' + self.new_edge + ' ' + tok2] += 1

                elif self.tryRA(tr, tr.amr, gold_amr):
                    tr.RA(edge_label=self.new_edge)
                    tok1 = tr.amr.nodes[stack1]
                    tok2 = tr.amr.nodes[stack0]
                    self.stats['RA'][tok1 + ' ' + self.new_edge + ' ' + tok2] += 1

                elif self.tryReduce(tr, tr.amr, gold_amr):
                    tok = tr.amr.nodes[stack0]
                    self.stats['REDUCE'][tok] += 1
                    tr.REDUCE()

                elif self.trySWAP(tr, tr.amr, gold_amr):
                    tr.SWAP()
                    tok1 = tr.amr.nodes[stack1]
                    tok2 = tr.amr.nodes[stack0]
                    self.stats['SWAP']['swapped: '+tok1 + ' stack0: ' + tok2] += 1

                elif tr.buffer:
                    tr.SHIFT()

                else:
                    tr.stack = []
                    tr.buffer = []
                    break
            tr.CLOSE(training=True, gold_amr=gold_amr, use_addnonde_rules=use_addnode_rules)
            if graph_file:
                with open(graph_file, 'a+', encoding='utf8') as f:
                    f.write(tr.amr.toJAMRString())
            if action_file:
                with open(action_file, 'a+', encoding='utf8') as f:
                    f.write(str(tr))
            del gold_amr.nodes[-1]
        print_log("oracle", "Done")

    """
    Check if the next action is CONFIRM

    If the gold node label is different from the assigned label,
    return the gold label.
    """

    def tryConfirm(self, transitions, amr, gold_amr):

        if not transitions.stack:
            return False

        stack0 = transitions.stack[-1]

        tok_alignment = gold_amr.alignmentsToken2Node(stack0)
        if 'DEPENDENT' not in transitions.actions[-1] and len(tok_alignment) != 1:
            return False

        if stack0 in transitions.entities:
            return False

        if len(tok_alignment) == 1:
            gold_id = tok_alignment[0]
        else:
            gold_id = gold_amr.findSubGraph(tok_alignment).root
        isPred = stack0 not in transitions.is_confirmed

        if isPred:
            new_node = gold_amr.nodes[gold_id]
            old_node = amr.nodes[stack0]

            if old_node not in self.possiblePredicates:
                self.possiblePredicates[old_node] = Counter()
            if new_node not in self.preds2Ints:
                self.preds2Ints.setdefault(new_node, len(self.preds2Ints))
            self.possiblePredicates[old_node][new_node] += 1
            self.new_node = new_node
        return isPred

    """
    Check if the next action is LA (left arc)

    If there is an unpredicted edge from stack[-1] to stack[-2]
    return the edge label.
    """

    def tryLA(self, transitions, amr, gold_amr):

        if len(transitions.stack) < 2:
            return False

        # check if we should MERGE instead
        if len(transitions.buffer) > 0:
            buffer0 = transitions.buffer[-1]
            stack0 = transitions.stack[-1]
            if self.tryMerge(transitions, amr, gold_amr, first=stack0, second=buffer0):
                return False

        head = transitions.stack[-1]
        dependent = transitions.stack[-2]
        isLeftHead, labelL = self.isHead(amr, gold_amr, head, dependent)

        if isLeftHead:
            self.new_edge = labelL
        return isLeftHead

    """
    Check if the next action is RA (right arc)

    If there is an unpredicted edge from stack[-2] to stack[-1]
    return the edge label.
    """

    def tryRA(self, transitions, amr, gold_amr):

        if len(transitions.stack) < 2:
            return False

        # check if we should MERGE instead
        if len(transitions.buffer) > 0:
            buffer0 = transitions.buffer[-1]
            stack0 = transitions.stack[-1]
            if self.tryMerge(transitions, amr, gold_amr, first=stack0, second=buffer0):
                return False

        head = transitions.stack[-2]
        dependent = transitions.stack[-1]
        isRightHead, labelR = self.isHead(amr, gold_amr, head, dependent)

        if isRightHead:
            self.new_edge = labelR
        return isRightHead

    """
    Check if the next action is REDUCE

    If
    1) there is nothing aligned to a token, or
    2) all gold edges are already predicted for thet token,
    then return True.
    """

    def tryReduce(self, transitions, amr, gold_amr, node_id=None):

        if not transitions.stack and not node_id:
            return False

        stack0 = transitions.stack[-1]

        node_id = stack0 if not node_id else id

        tok_alignment = gold_amr.alignmentsToken2Node(node_id)
        if len(tok_alignment) == 0:
            return True

        # check if we should merge instead (i.e. the alignment is the same as the next token)
        if transitions.buffer:
            buffer0 = transitions.buffer[-1]
            buffer0_alignment = gold_amr.alignmentsToken2Node(buffer0)
            if buffer0_alignment == tok_alignment:
                return False

        if len(tok_alignment) == 1:
            gold_id = tok_alignment[0]
        else:
            gold_id = gold_amr.findSubGraph(tok_alignment).root

        # check if all edges are already predicted

        countSource = 0
        countTarget = 0
        countSourceGold = 0
        countTargetGold = 0
        for s, r, t in amr.edges:
            if r == 'entity':
                continue
            if s == node_id:
                countSource += 1
            if t == node_id:
                countTarget += 1
        for s, r, t in gold_amr.edges:
            if s == gold_id:
                countSourceGold += 1
            if t == gold_id:
                countTargetGold += 1
        if node_id in transitions.entities:
            for s, r, t in gold_amr.edges:
                if s == gold_id and t in tok_alignment:
                    countSource += 1
                if t == gold_id and s in tok_alignment:
                    countTarget += 1
        if countSourceGold == countSource and countTargetGold == countTarget:
            return True
        return False

    """
    Check if the next action is MERGE

    Merge if two tokens have the same alignment.
    """

    def tryMerge(self, transitions, amr, gold_amr, first=None, second=None):
        if not first or not second:
            if len(transitions.stack) < 2:
                return False

            first = transitions.stack[-1]
            second = transitions.stack[-2]

        if first == second:
            return False

        first_alignment = gold_amr.alignmentsToken2Node(first)
        second_alignment = gold_amr.alignmentsToken2Node(second)
        if not first_alignment or not second_alignment:
            return False
        if first_alignment == second_alignment:
            return True
        if set(first_alignment).intersection(set(second_alignment)):
            return True
        return False

    """
    Check if the next action is SWAP

    SWAP if there is an unpredicted gold edge between stack[-1]
    and some other node in the stack (blocked by stack[-2])
    or if stack1 can be reduced.
    """

    def trySWAP(self, transitions, amr, gold_amr):

        if len(transitions.stack) < 2:
            return False

        stack0 = transitions.stack[-1]
        stack1 = transitions.stack[-2]

        if stack0 in transitions.swapped_words and stack1 in transitions.swapped_words.get(stack0):
            return False
        if stack1 in transitions.swapped_words and stack0 in transitions.swapped_words.get(stack1):
            return False

        # check if we should MERGE instead
        if len(transitions.buffer) > 0:
            buffer0 = transitions.buffer[-1]
            if self.tryMerge(transitions, amr, gold_amr, first=stack0, second=buffer0):
                return False

        tok_alignment = gold_amr.alignmentsToken2Node(stack0)

        for tok in transitions.stack:
            if tok == stack1 or tok == stack0:
                continue
            isHead, labelL = self.isHead(amr, gold_amr, stack0, tok)
            if isHead:
                return True
            isHead, labelR = self.isHead(amr, gold_amr, tok, stack0)
            if isHead:
                return True
            # check if we need to merge two tokens separated by stack1
            k_alignment = gold_amr.alignmentsToken2Node(tok)
            if k_alignment == tok_alignment:
                return True
        # if not REPLICATE and self.tryReduce(transitions, amr, gold_amr, stack1):
        #     return True
        return False

    """
    Check if the next action is DEPENDENT


    Only for :polarity and :mode, if an edge and node is aligned
    to this token in the gold amr but does not exist in the predicted amr,
    the oracle adds it using the DEPENDENT action.
    """

    def tryDependent(self, transitions, amr, gold_amr):

        if not transitions.stack:
            return False

        stack0 = transitions.stack[-1]
        tok_alignment = gold_amr.alignmentsToken2Node(stack0)

        if not tok_alignment:
            return False

        if len(tok_alignment) == 1:
            source = tok_alignment[0]
        else:
            source = gold_amr.findSubGraph(tok_alignment).root

        for s, r, t in gold_amr.edges:
            if s == source and r in [":polarity", ":mode"]:
                if (stack0, r) in [(e[0], e[1]) for e in amr.edges]:
                    continue
                if t not in tok_alignment and (t in gold_amr.alignments and gold_amr.alignments[t]):
                    continue
                self.new_edge = r
                self.new_node = gold_amr.nodes[t]
                return True
        return False

    """
    Check if the next action is ENTITY
    """

    def tryEntity(self, transitions, amr, gold_amr):

        if not transitions.stack:
            return False

        stack0 = transitions.stack[-1]

        # check if already an entity
        if stack0 in transitions.entities:
            return False

        tok_alignment = gold_amr.alignmentsToken2Node(stack0)

        # check if alignment empty (or singleton)
        if len(tok_alignment) <= 1:
            return False

        # check if we should MERGE instead
        if len(transitions.stack) > 1:
            id = transitions.stack[-2]
            if self.tryMerge(transitions, amr, gold_amr, first=stack0, second=id):
                return False
        for id in reversed(transitions.buffer):
            if self.tryMerge(transitions, amr, gold_amr, first=stack0, second=id):
                return False

        edges = gold_amr.findSubGraph(tok_alignment).edges
        if not edges:
            return False

        # check if we should use DEPENDENT instead
        if len(tok_alignment) == 2:
            if len(edges) == 1 and edges[0][1] in [':mode', ':polarity']:
                return False

        final_nodes = [n for n in tok_alignment if not any(s == n for s, r, t in edges)]
        new_nodes = [gold_amr.nodes[n] for n in tok_alignment if n not in final_nodes]

        self.entity_type = ','.join(new_nodes)
        self.possibleEntityTypes[self.entity_type] += 1

        return True

    """
    Check if the x is the head of y in the gold AMR graph

    If (the root of) x has an edge to (the root of) y in the gold AMR
    which is not in the predicted AMR, return True.
    """

    def isHead(self, amr, gold_amr, x, y):

        x_alignment = gold_amr.alignmentsToken2Node(x)
        y_alignment = gold_amr.alignmentsToken2Node(y)

        if not y_alignment or not x_alignment:
            return False, ''
        # get root of subgraph aligned to x
        if len(x_alignment) > 1:
            source = gold_amr.findSubGraph(x_alignment).root
        else:
            source = x_alignment[0]
        # get root of subgraph aligned to y
        if len(y_alignment) > 1:
            target = gold_amr.findSubGraph(y_alignment).root
        else:
            target = y_alignment[0]

        for s, r, t in gold_amr.edges:
            if source == s and target == t:
                # check if already assigned
                if (x, r, y) not in amr.edges:
                    return True, r
        return False, ''

    def tryIntroduce(self, transitions, amr, gold_amr):
        if not transitions.stack or not transitions.latent:
            return False
        stack0 = transitions.stack[-1]

        # check if we should MERGE instead
        if len(transitions.buffer) > 0:
            buffer0 = transitions.buffer[-1]
            stack0 = transitions.stack[-1]
            if self.tryMerge(transitions, amr, gold_amr, first=stack0, second=buffer0):
                return False

        if not transitions.stack and transitions.latent:
            return True

        idx = len(transitions.latent)-1
        for latentk in reversed(transitions.latent):
            isHead, label = self.isHead(amr, gold_amr, stack0, latentk)

            if isHead:
                # rearrange latent if necessary
                transitions.latent.append(transitions.latent.pop(idx))
                return True
            isHead, label = self.isHead(amr, gold_amr, latentk, stack0)

            if isHead:
                # rearrange latent if necessary
                transitions.latent.append(transitions.latent.pop(idx))
                return True
            idx -= 1
        return False


if __name__ == '__main__':

    input_file = sys.argv[1]
    gfile = sys.argv[2] if len(sys.argv) > 2 else 'oracle_amrs.txt'
    afile = sys.argv[3] if len(sys.argv) > 3 else 'oracle_actions.txt'

    cr = JAMR_CorpusReader()
    cr.load_amrs(input_file)

    oracle = AMR_Oracle(verbose=True)
    print_log("amr", "Processing oracle")
    oracle.runOracle(cr.amrs, action_file=afile, graph_file=gfile, add_unaligned=0)
    for stat in oracle.stats:
        print_log("amr", stat)
        print_log("amr", oracle.stats[stat].most_common(100))
        print_log("amr", "")

    if use_addnode_rules:
        for x in transitions.entity_rule_totals:
            perc = transitions.entity_rule_stats[x]/transitions.entity_rule_totals[x]
            print(x,  transitions.entity_rule_stats[x], '/', transitions.entity_rule_totals[x], '=', f'{perc:.2f}')
        perc = sum(transitions.entity_rule_stats.values())/sum(transitions.entity_rule_totals.values())
        print('Totals:', f'{perc:.2f}')

        print()
        print('Failed Entity Predictions:')
        print(transitions.entity_rule_fails.most_common(1000))
