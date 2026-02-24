# UNIPI – UERANSIM Experimental Framework

This repository is part of the Master's Thesis:

**Post-Quantum Cryptography in 5G Core Networks: Implementation and Cost Analysis in Open5GS**

This repository is based on the original UERANSIM project and is used as the UE and gNodeB simulation environment within the experimental 5G SA testbed.

The purpose of this repository is to integrate the UERANSIM project into a fully automated and reproducible benchmarking framework for evaluating the impact of Post-Quantum Cryptography on 5G Core procedures.


# Important! – Original Installation Required

Before using this repository, you must first install UERANSIM by following the official installation guide provided by the original project:

[https://github.com/aligungr/UERANSIM](https://github.com/aligungr/UERANSIM)

This repository does **not** replace the original installation procedure.
It assumes that UERANSIM is correctly installed and operational according to the upstream documentation.


# Overview

Within the thesis testbed described in Chapter 5 and Chapter 6, UERANSIM is used to emulate:

* 5G User Equipment (UE)
* 5G gNodeB (gNB)

These components interact with the modified Open5GS Core Network in a 5G Stand-Alone architecture.

This repository extends the usage of UERANSIM by providing:

* Automated UE registration scripts
* Measurements results


# measurements_scripts directory

The `measurements_scripts/` directory contains the automation layer used to execute all UE-side experiments described in the thesis.

These scripts allow:

* Running single UE registration experiments
* Running batch UE registration experiments (multiple UEs sequentially or concurrently)
* Controlling UE startup timing
* Coordinating gNB and UE lifecycle
* Automatically collecting UE-side logs


# measurements_results directory

The `measurements_results/` directory contains:

* Raw UE logs from all experimental campaigns
* Structured CSV files derived from UE registration timings
* Organized datasets for single and batch modes
* Data used for latency analysis presented in Chapter 6 

These results correspond to:

* UE Registration Latency measurements
* Batch Registration behavior
* Failure cases related to timer expirations (e.g. T3510)


# Reproducibility

To reproduce the UE-side experiments:

1. Install UERANSIM following the official upstream guide
2. Configure it according to the thesis testbed topology
3. Use the provided scripts in `measurements_scripts/`
4. Collect structured results inside `measurements_results/`

This repository provides:

* A structured UE automation framework
* Reproducible experiment execution
* Complete UE-side datasets


# Scope

This repository is intended for:

* Researchers evaluating 5G Core performance under cryptographic modifications
* Experimental 5G SA testbeds
* UE registration latency analysis
* Reproducible performance benchmarking

It is not intended to replace or fork UERANSIM's core implementation, nor to provide production-ready modifications.