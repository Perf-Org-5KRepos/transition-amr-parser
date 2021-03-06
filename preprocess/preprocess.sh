#!/bin/bash


echo -e "\nRunning Preprocessing Script: process will take ~1 hour\n"


> output
> error-log.out

# process files
echo -e "\nMerge files\n"
python merge_files.py $1

echo -e "\nRemove wikis\n"
python remove_wiki.py


# JAMR
echo -e "\nDownloading JAMR\n"
git clone https://github.com/jflanigan/jamr.git
cd jamr
git checkout Semeval-2016 >> output 2>> error-log.out
./setup >> output 2>> error-log.out
. scripts/config.sh >> output 2>> error-log.out


echo -e "\nRunning JAMR\n"
scripts/ALIGN.sh < ../train.no_wiki.txt > ../train.jamr.txt 2>> error-log.out
scripts/ALIGN.sh < ../dev.no_wiki.txt > ../dev.jamr.txt 2>> error-log.out
scripts/ALIGN.sh < ../test.no_wiki.txt > ../test.jamr.txt 2>> error-log.out

cd ..


echo -e "\nBuild Kevin input files\n"
python jamr_2_kevin.py


# Kevin
echo -e "\nDownloading Kevin\n"
wget https://www.isi.edu/~damghani/papers/Aligner.zip
unzip Aligner.zip >> output 2>> error-log.out
mv Publish_Version kevin
rm Aligner.zip
cd kevin
git clone https://github.com/moses-smt/mgiza.git
cd mgiza/mgizapp
cmake . >> output 2>> error-log.out
make >> output 2>> error-log.out
make install >> output 2>> error-log.out

cd ../..
# set python2
pip install virtualenv
virtualenv -p /usr/bin/python2.7 python2-env
source python2-env/bin/activate


echo -e "\nRunning Kevin\n"

echo -e "AMR=../train.amrs.txt\nENG=../train.sents.txt\nMGIZA_SCRIPT=mgiza/mgizapp/scripts\nMGIZA_BIN=mgiza/mgizapp/bin\n" > addresses.keep
bash run.sh >> output 2>> error-log.out
cp AMR_Aligned.keep ../train.kevin.txt

echo -e "AMR=../dev.amrs.txt\nENG=../dev.sents.txt\nMGIZA_SCRIPT=mgiza/mgizapp/scripts\nMGIZA_BIN=mgiza/mgizapp/bin\n" > addresses.keep
bash run.sh >> output 2>> error-log.out
cp AMR_Aligned.keep ../dev.kevin.txt

echo -e "AMR=../test.amrs.txt\nENG=../test.sents.txt\nMGIZA_SCRIPT=mgiza/mgizapp/scripts\nMGIZA_BIN=mgiza/mgizapp/bin\n" > addresses.keep
bash run.sh >> output 2>> error-log.out
cp AMR_Aligned.keep ../test.kevin.txt

deactivate
rm -r python2-env
cd ..


# merge alignments
echo -e "\nClean Kevin output\n"
python clean_kevin.py


echo -e "\nMerge Alignments\n"
cd merge_scripts
bash run_jtok.sh ../train.kevin.txt ../train.jamr.txt
mv ../train.kevin.txt.mrged ../train.merged.txt

bash run_jtok.sh ../dev.kevin.txt ../dev.jamr.txt
mv ../dev.kevin.txt.mrged ../dev.merged.txt

bash run_jtok.sh ../test.kevin.txt ../test.jamr.txt
mv ../test.kevin.txt.mrged ../test.merged.txt
cd ..


echo -e "\nClean\n"
python clean.py
rm 1 2 3 4 5 6 7 8

echo -e "\nCopying data to ../data\n"
cp train.merged.txt ../data/train.txt
cp dev.merged.txt ../data/dev.txt
cp test.merged.txt ../data/test.txt
echo -e "\nFinished\n"
