# -*- coding: utf-8
"""
    Classes for pan operations.

    anvi-pan-genome is the default client of this module
"""

import os
import tempfile

import anvio
import anvio.tables as t
import anvio.utils as utils
import anvio.dbops as dbops
import anvio.terminal as terminal
import anvio.filesnpaths as filesnpaths

from anvio.errors import ConfigError, FilesNPathsError


__author__ = "A. Murat Eren"
__copyright__ = "Copyright 2016, The anvio Project"
__credits__ = []
__license__ = "GPL 3.0"
__version__ = anvio.__version__
__maintainer__ = "A. Murat Eren"
__email__ = "a.murat.eren@gmail.com"


run = terminal.Run()
progress = terminal.Progress()
pp = terminal.pretty_print


class Pangenome:
    def __init__(self, args = None, run = run, progress = progress):
        self.run = run
        self.progress = progress

        A = lambda x: args.__dict__[x] if args.__dict__.has_key(x) else None
        input_file_for_contig_dbs = A('input_contig_dbs')
        self.num_threads = A('num_threads')
        self.output_dir = A('output_dir')
        self.overwrite_output_destinations = A('overwrite_output_destinations')
        self.debug = A('debug')

        self.temp_files_to_remove_later = []

        self.contig_dbs = utils.get_TAB_delimited_file_as_dictionary(input_file_for_contig_dbs, expected_fields = ['name', 'path']) if input_file_for_contig_dbs else {}

        # convert relative paths to absolute paths
        for contigs_db in self.contig_dbs:
            path = self.contig_dbs[contigs_db]['path']
            if not path.startswith('/'):
                self.contig_dbs[contigs_db]['path'] = os.path.abspath(os.path.join(os.path.dirname(input_file_for_contig_dbs), path))


    def get_output_file_path(self, file_name, temp_file = False):
        output_file_path = os.path.join(self.output_dir, file_name)

        if temp_file:
            self.temp_files_to_remove_later.append(output_file_path)

        return output_file_path


    def remove_temp_files(self):
        if self.debug:
            return

        for file_path in self.temp_files_to_remove_later:
            os.remove(file_path)


    def check_programs(self):
        utils.is_program_exists('diamond')


    def check_params(self):
        # deal with the output directory:
        try:
            filesnpaths.is_file_exists(self.output_dir)
        except FilesNPathsError:
            filesnpaths.gen_output_directory(self.output_dir, delete_if_exists = self.overwrite_output_destinations)

        filesnpaths.is_output_dir_writable(self.output_dir)
        self.output_dir = os.path.abspath(self.output_dir)


    def init_contig_dbs(self):
        if type(self.contig_dbs) != type({}):
            raise ConfigError, "self.contig_dbs must be of type dict. Anvi'o needs an adult :("

        if not len(self.contig_dbs):
            raise ConfigError, "There is no contig databases to process..."

        if len(self.contig_dbs) < 2:
            raise ConfigError, "There must be at least two contigs databases for this to work :/"

        if len([c for c in self.contig_dbs.values() if 'path' not in c]):
            raise ConfigError, "self.contig_dbs does not seem to be a properly formatted dictionary for\
                                the anvi'o class Pangenome. You did something very wrong."

        missing_dbs = [c['path'] for c in self.contig_dbs.values() if not os.path.exists(c['path'])]
        if len(missing_dbs):
            raise ConfigError, "%d of %d of your contigs databases are not found where they were supposed to be \
                                based on the description you provided :( Here is one that is missing: '%s'" \
                                                % (len(missing_dbs), len(self.contig_dbs), missing_dbs[0])

        # just go over the contig dbs to make sure they all are OK.
        for contigs_db_name in self.contig_dbs:
            c = self.contig_dbs[contigs_db_name]
            c['name'] = contigs_db_name
            contigs_db = dbops.ContigsDatabase(c['path'])
            for key in contigs_db.meta:
                c[key] = contigs_db.meta[key]
            contigs_db.disconnect()

        # if two contigs db has the same hash, we are kinda f'd:
        if len(set([c['contigs_db_hash'] for c in self.contig_dbs.values()])) != len(self.contig_dbs):
            raise ConfigError, 'Not all hash values are unique across all contig databases you provided. Something\
                                very fishy is going on :/'

        # make sure genes are called in every contigs db:
        if len([c['genes_are_called'] for c in self.contig_dbs.values()]) != len(self.contig_dbs):
            raise ConfigError, 'Genes are not called in every contigs db in the collection :/'

        self.run.info('Contig DBs', '%d contig databases have been found.' % len(self.contig_dbs))


    def gen_combined_proteins_fasta(self):
        self.progress.new('Storing combined protein sequences')
        output_file_path = self.get_output_file_path('combined_proteins.fa', temp_file = True)
        output_file = open(output_file_path, 'w')

        for c in self.contig_dbs.values():
            self.progress.update('Working on %s ...' % c['name'])
            num_genes = 0
            contigs_db = dbops.ContigsDatabase(c['path'])
            protein_sequences = contigs_db.db.get_table_as_dict(t.gene_protein_sequences_table_name)
            for protein_id in protein_sequences:
                num_genes += 1
                output_file.write('>%s_%d\n' % (c['contigs_db_hash'], protein_id))
                output_file.write('%s\n' % protein_sequences[protein_id]['sequence'])
            contigs_db.disconnect()
            c['num_genes'] = num_genes

        output_file.close()
        self.progress.end()

        self.run.info('ORFs', '%s protein sequences are stored for analysis.' % pp(sum([c['num_genes'] for c in self.contig_dbs.values()])))

        return output_file_path


    def run_diamond(self, combined_proteins_fasta_path):
        diamond = Diamond(combined_proteins_fasta_path, run = self.run, progress = self.progress,
                          num_threads = self.num_threads, overwrite_output_destinations = self.overwrite_output_destinations)

        diamond.log_file_path = self.get_output_file_path('log.txt')
        diamond.target_db_path = self.get_output_file_path('.'.join(combined_proteins_fasta_path.split('.')[:-1]))
        diamond.search_output_path = self.get_output_file_path('diamond-search-results')
        diamond.tabular_output_path = self.get_output_file_path('diamond-search-results.txt')

        diamond.get_blastall_results()


    def sanity_check(self):
        self.check_programs()
        self.check_params()
        self.init_contig_dbs()


    def process(self):
        self.sanity_check()

        # first we will export all proteins 
        combined_proteins_fasta_path = self.gen_combined_proteins_fasta()

        # run diamond
        self.run_diamond(combined_proteins_fasta_path)


class Diamond:
    def __init__(self, query_fasta, run = run, progress = progress, num_threads = 1, overwrite_output_destinations = False):
        self.run = run
        self.progress = progress

        self.num_threads = num_threads
        self.overwrite_output_destinations = overwrite_output_destinations

        utils.is_program_exists('diamond')

        self.tmp_dir = tempfile.gettempdir()

        self.query_fasta = query_fasta
        self.log_file_path = 'diamond-log-file.txt'
        self.target_db_path = 'diamond-target'
        self.search_output_path = 'diamond-search-resuults'
        self.tabular_output_path = 'diamond-search-results.txt'


    def get_blastall_results(self):
        force_makedb, force_blastp, force_view = False, False, False

        if self.overwrite_output_destinations:
            force_makedb = True

        if os.path.exists(self.target_db_path + '.dmnd') and not force_makedb:
            run.info_single("Notice: A diamond database is found in the output directory, and will be used!", mc = 'red', nl_before = 1)
        else:
            self.makedb()
            force_blastp, forrce_view = True, True

        if os.path.exists(self.search_output_path + '.daa') and not force_blastp:
            run.info_single("Notice: A DIAMOND search result is found in the output directory: skipping BLASTP!", mc = 'red', nl_before = 1)
        else:
            self.blastp()
            force_view = True

        if os.path.exists(self.tabular_output_path) and not force_view:
            run.info_single("Notice: A DIAMOND tabular output is found in the output directory. Anvi'o will not generate another one!", mc = 'red', nl_before = 1)
        else:
            self.view()

        return self.tabular_output_path


    def check_output(self, expected_output, process = 'diamond'):
        if not os.path.exists(expected_output):
            self.progress.end()
            raise ConfigError, "Pfft. Something probably went wrong with Diamond's '%s' since one of the expected output files are missing.\
                                Please check the log file here: '%s." % (process, self.log_file_path)


    def makedb(self):
        self.progress.new('DIAMOND')
        self.progress.update('creating the search database (using %d thread(s)) ...' % self.num_threads)
        cmd_line = ('diamond makedb --in %s -d %s -p %d >> "%s" 2>&1' % (self.query_fasta,
                                                                         self.target_db_path,
                                                                         self.num_threads,
                                                                         self.log_file_path))

        with open(self.log_file_path, "a") as log: log.write('CMD: ' + cmd_line + '\n')

        utils.run_command(cmd_line)

        self.progress.end()

        expected_output = self.target_db_path + '.dmnd'
        self.check_output(expected_output, 'makedb')

        self.run.info('Diamond temp search db', expected_output)


    def blastp(self):
        self.progress.new('DIAMOND')
        self.progress.update('running blastp (using %d thread(s)) ...' % self.num_threads)
        cmd_line = ('diamond blastp -q %s -d %s -a %s -t %s -p %d >> "%s" 2>&1' % (self.query_fasta,
                                                                                   self.target_db_path,
                                                                                   self.search_output_path,
                                                                                   self.tmp_dir,
                                                                                   self.num_threads,
                                                                                   self.log_file_path))
        with open(self.log_file_path, "a") as log: log.write('CMD: ' + cmd_line + '\n')

        utils.run_command(cmd_line)

        self.progress.end()

        expected_output = self.search_output_path + '.daa'
        self.check_output(expected_output, 'blastp')

        self.run.info('Diamond blastp results', expected_output)


    def view(self):
        self.progress.new('DIAMOND')
        self.progress.update('generating tabular output (using %d thread(s)) ...' % self.num_threads)
        cmd_line = ('diamond view -a %s -o %s -p %d >> "%s" 2>&1' % (self.search_output_path + '.daa',
                                                                     self.tabular_output_path,
                                                                     self.num_threads,
                                                                     self.log_file_path))
        with open(self.log_file_path, "a") as log: log.write('CMD: ' + cmd_line + '\n')

        utils.run_command(cmd_line)

        self.progress.end()

        self.check_output(self.tabular_output_path, 'view')

        self.run.info('Diamond tabular output file', self.tabular_output_path)