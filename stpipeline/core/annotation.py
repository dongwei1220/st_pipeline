""" 
This module contains wrappers of htseq-count
with slight modifications to perform annotation
of ST mapped reads against a reference.
"""
import logging
import os
import pysam
from stpipeline.common.utils import fileOk
from stpipeline.common.stats import qa_stats
import itertools
import HTSeq

class UnknownChrom( Exception ):
    pass

def invert_strand( iv ):
    iv2 = iv.copy()
    if iv2.strand == "+":
        iv2.strand = "-"
    elif iv2.strand == "-":
        iv2.strand = "+"
    else:
        raise ValueError, "Illegal strand"  
    return iv2

def count_reads_in_features(sam_filename, 
                            gff_filename, 
                            samtype, 
                            order, 
                            stranded,
                            overlap_mode, 
                            feature_type, 
                            id_attribute, 
                            quiet, 
                            minaqual, 
                            samout, 
                            include_non_annotated=False, 
                            htseq_no_ambiguous=True):
    """
    This is taken from the function count_reads_in_features() from the 
    script htseq-count in the HTSeq package version 0.61.p2 
    The reason to do so is to fix two really small bugs related to the SAM output.
    The code of the function is small and simple so for now we
    will use the patched function here. A patch request has been sent
    to the HTSeq team.
    The description of the parameters are the same as htseq-count.
    Two parameters were added to filter out what to write in the sam output
    
    The HTSEQ License
    HTSeq is free software: you can redistribute it and/or modify it under the terms of 
    the GNU General Public License as published by the Free Software Foundation, 
    either version 3 of the License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful, 
    but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY 
    or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

    The full text of the GNU General Public License, version 3, 
    can be found here: http://www.gnu.org/licenses/gpl-3.0-standalone.html
    """
    # Set up the filters
    count_reads_in_features.filter_htseq = \
    ["__too_low_aQual", "__not_aligned", "__alignment_not_unique"]
    if not include_non_annotated: 
        count_reads_in_features.filter_htseq.append("__no_feature")
    count_reads_in_features.filter_htseq_no_ambiguous = htseq_no_ambiguous

    # Open SAM output file
    flag_write = "wb" if samtype == "bam" else "wh"
    flag_read = "rb" if samtype == "bam" else "r"
    saminfile = pysam.AlignmentFile(sam_filename, flag_read)
    count_reads_in_features.samoutfile = pysam.AlignmentFile(samout, flag_write, template=saminfile)
    saminfile.close()
    # Counter of annotated records
    count_reads_in_features.annotated = 0
    
    # Function to write to SAM output
    def write_to_samout(r, assignment):
        if not pe_mode:
            r = (r,)
        for read in r:
            if read is not None and assignment not in count_reads_in_features.filter_htseq \
            and not (count_reads_in_features.filter_htseq_no_ambiguous and assignment.find("__ambiguous") != -1):
                sam_record = read.to_pysam_AlignedRead(count_reads_in_features.samoutfile)
                sam_record.set_tag("XF", assignment, "Z")
                count_reads_in_features.samoutfile.write(sam_record)
                count_reads_in_features.annotated +=1
                
    # Annotation objects
    features = HTSeq.GenomicArrayOfSets("auto", stranded != "no")
    counts = {}
    gff = HTSeq.GFF_Reader(gff_filename)   

    try:
        for f in gff:
            if f.type == feature_type:
                try:
                    feature_id = f.attr[id_attribute]
                except KeyError:
                    raise ValueError, ("Feature %s does not contain a '%s' attribute" \
                                       % (f.name, id_attribute))
                if stranded != "no" and f.iv.strand == ".":
                    raise ValueError, ("Feature %s at %s does not have strand information but you are " \
                                       "running htseq-count in stranded mode. Use '--stranded=no'." % 
                                       (f.name, f.iv))
                features[f.iv] += feature_id
                counts[f.attr[id_attribute]] = 0
    except:
        raise
    
    if len(counts) == 0:
        raise RuntimeError, "No features of type '%s' found.\n" % feature_type
        
    if samtype == "sam":
        SAM_or_BAM_Reader = HTSeq.SAM_Reader
    elif samtype == "bam":
        SAM_or_BAM_Reader = HTSeq.BAM_Reader
    else:
        raise ValueError, "Unknown input format %s specified." % samtype

    try:
        read_seq_file = SAM_or_BAM_Reader(sam_filename)
        read_seq = read_seq_file
        first_read = iter(read_seq).next()
        pe_mode = first_read.paired_end
    except:
        raise RuntimeError, "Error occurred when reading beginning of SAM/BAM file."

    try:
        if pe_mode:
            if order == "name":
                read_seq = HTSeq.pair_SAM_alignments(read_seq)
            elif order == "pos":
                read_seq = HTSeq.pair_SAM_alignments_with_buffer(read_seq)
            else:
                raise ValueError, "Illegal order specified."

        for r in read_seq:
                
            if not pe_mode:
                if not r.aligned:
                    write_to_samout(r, "__not_aligned")
                    continue
                
                try:
                    if r.optional_field("NH") > 1:
                        write_to_samout(r, "__alignment_not_unique")
                        continue
                except KeyError:
                    pass
                
                if r.aQual < minaqual:
                    write_to_samout(r, "__too_low_aQual")
                    continue
                
                if stranded != "reverse":
                    iv_seq = (co.ref_iv for co in r.cigar if co.type == "M" and co.size > 0)
                else:
                    iv_seq = (invert_strand(co.ref_iv) 
                              for co in r.cigar 
                              if co.type == "M" and co.size > 0)            
            else:
                if r[0] is not None and r[0].aligned:
                    if stranded != "reverse":
                        iv_seq = (co.ref_iv 
                                  for co in r[0].cigar 
                                  if co.type == "M" and co.size > 0)
                    else:
                        iv_seq = (invert_strand(co.ref_iv) 
                                  for co in r[0].cigar 
                                  if co.type == "M" and co.size > 0)
                else:
                    iv_seq = tuple()
                
                if r[1] is not None and r[1].aligned:            
                    if stranded != "reverse":
                        iv_seq = itertools.chain(iv_seq,
                                                 (invert_strand(co.ref_iv) 
                                                  for co in r[1].cigar 
                                                  if co.type == "M" and co.size > 0))
                    else:
                        iv_seq = itertools.chain(iv_seq, 
                                                 (co.ref_iv 
                                                  for co in r[1].cigar 
                                                  if co.type == "M" and co.size > 0))
                else:
                    if (r[0] is None) or not (r[0].aligned):
                        write_to_samout(r, "__not_aligned")
                        continue         
                try:
                    if (r[0] is not None and r[0].optional_field("NH") > 1) \
                    or (r[1] is not None and r[1].optional_field("NH") > 1):
                        write_to_samout(r, "__alignment_not_unique")
                        continue
                except KeyError:
                    pass
                
                if (r[0] and r[0].aQual < minaqual) or (r[1] and r[1].aQual < minaqual):
                    write_to_samout(r, "__too_low_aQual")
                    continue         
               
            try:
                if overlap_mode == "union":
                    fs = set()
                    for iv in iv_seq:
                        if iv.chrom not in features.chrom_vectors:
                            raise UnknownChrom
                        for iv2, fs2 in features[ iv ].steps():
                            fs = fs.union(fs2)
                elif overlap_mode == "intersection-strict" or overlap_mode == "intersection-nonempty":
                    fs = None
                    for iv in iv_seq:
                        if iv.chrom not in features.chrom_vectors:
                            raise UnknownChrom
                        for iv2, fs2 in features[ iv ].steps():
                            if len(fs2) > 0 or overlap_mode == "intersection-strict":
                                if fs is None:
                                    fs = fs2.copy()
                                else:
                                    fs = fs.intersection(fs2)
                else:
                    raise RuntimeError, "Illegal overlap mode."
                
                if fs is None or len(fs) == 0:
                    write_to_samout(r, "__no_feature")
                elif len(fs) > 1:
                    write_to_samout(r, "__ambiguous[" + '+'.join(fs) + "]")
                else:
                    write_to_samout(r, list(fs)[0])
                    
            except UnknownChrom:
                write_to_samout(r, "__no_feature")

    except:
        count_reads_in_features.samoutfile.close()
        raise

    count_reads_in_features.samoutfile.close()
    return count_reads_in_features.annotated

def annotateReads(mappedReads, 
                  gtfFile,
                  outputFile,
                  mode,
                  strandness="reverse",
                  htseq_no_ambiguous=True, 
                  include_non_annotated=False):
    """
    Annotates a file with mapped reads (SAM/BAM) using a modified 
    version of the htseq-count tool. It writes the annotated records to a file.
    :param mappedReads: path to a SAM/BAM file with mapped reads sorted by coordinate
    :param gtfFile: path to an annotation file in GTF format
    :param mode: htseq-count overlapping mode (see htseq-count documentation)
    :param strandness: the type of strandness to use when annotating (yes, no or reverse)
    :param htseq_no_ambiguous: true if we want to discard ambiguous annotations
    :param include_non_annotated: true if we want to include 
    non annotated reads as __no_feature in the output
    :param outputFile: the name/path to the output file
    :type mappedReads: str
    :type gtfFile: str
    :type mode: str
    :type strandness: str
    :type htseq_no_ambiguos: boolean
    :param include_non_annotated: boolean
    :param outputFile: str
    :raises: RuntimeError, ValueError
    """
    
    logger = logging.getLogger("STPipeline")
    
    if not os.path.isfile(mappedReads):
        error = "Error during annotation, input file not present {}\n".format(mappedReads)
        logger.error(error)
        raise RuntimeError(error)
    
    try:
        annotated = count_reads_in_features(mappedReads,
                                            gtfFile,
                                            "bam", # Type BAM for files
                                            "pos", # Order pos or name
                                            strandness, # Strand yes/no/reverse
                                            mode, # intersection_nonempty, union, intersection_strict
                                            "exon", # feature type in GFF
                                            "gene_id", # gene_id or gene_name
                                            True, # Quiet mode
                                            0, # Min quality score
                                            outputFile,
                                            include_non_annotated,
                                            htseq_no_ambiguous)
    except Exception as e:
        error = "Error during annotation. HTSEQ execution failed\n"
        logger.error(error)
        raise e
    
    if not fileOk(outputFile):
        error = "Error during annotation. Output file not present {}\n".format(outputFile)
        logger.error(error)
        raise RuntimeError(error)
    
    logger.info("Annotated reads: {}".format(annotated))
    qa_stats.reads_after_annotation = annotated
