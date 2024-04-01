from pymatgen.io.cif import CifParser
from pymatgen.io.vasp import Poscar
from pymatgen.core.structure import Structure
from pymatgen.analysis.structure_matcher import StructureMatcher
import numpy as np
import yaml
import subprocess, os
from pathlib import Path

def choose_pot(element):
    if element == 'Zr':
        return 'Zr_sv'
    if element == 'Ca':
        return 'Ca_sv'
    return element

def make_potcar():
    with open('POSCAR', 'r') as f:
        lines = f.readlines()
        comp = lines[5].strip().split()
    with open('POTCAR', 'w') as f:
        for element in comp:
            with open(os.path.join('pots', choose_pot(element), 'POTCAR'), 'r') as f_in:
                f.write(f_in.read())
def setup_files():
    if os.path.exists('OSZICAR'):
        os.remove('OSZICAR')
    make_potcar()

def check_distances(structure, threshold=0.4):
    distance_mat = structure.lattice.get_all_distances(structure.frac_coords, structure.frac_coords)
    distance_mat += 1000*np.identity(distance_mat.shape[0], dtype=float)
    return distance_mat.min() < threshold
    
def enthalpy(energy, composition, reference_energies):
    result = energy
    total = np.sum(list(composition.as_dict().values()))
    for element in composition:
        result -= composition[element.name] / total * reference_energies[element.name]
    return result    

def read_energy():
    try:
        with open(os.path.join('vasp', 'OSZICAR'), 'r') as f:
            last_line = f.readlines()[-1].strip()
            last_line = last_line[last_line.find('F=')+2:last_line.find('E0=')].strip()
            energy = float(last_line)
    except:
        return None
    return energy

def vasp_energy():
    #reference_energies = {
    #    'Re': -12.42453,
    #    'V': -8.94119,
    #    'Zr': -8.51973,
    #    'Ca': -1.92,
    #    'Fe': -8.237,
    #    'B': -6.703473333333333,
    #    'P': -5.404,
    #    'Sc': -6.20184,
    #    'Hf': -9.95767,
    #    'Be': -3.76536,
    #}
    with open(os.path.join('vasp', 'reference_energies.yml'), 'r') as f:
        reference_energies = yaml.safe_load(f)
    try:
        structure = Structure.from_file(os.path.join('vasp', 'POSCAR'))
    except:
        return None
    #if check_distances(structure):
    #    return None
    #Poscar(structure).write_file(os.path.join('vasp', 'POSCAR'))
    os.chdir('vasp')
    try:
        setup_files()
        out = subprocess.run([os.path.join('./run_vasp.sh')], capture_output=True)
        with open('vasp_output.txt', 'r') as f:
            success = 'Error' not in f.read()
    except Exception as e:
        print(f"Error while calculating energies: {e}")
        success = False
    finally:
        os.chdir('..')
    if success:
        result = read_energy()
        if result is None:
            return None
        n_atoms = np.sum(list(structure.composition.as_dict().values()))
        result /= n_atoms
        formation_e = enthalpy(result, structure.composition, reference_energies)
        return {"e": result, "e_f": formation_e}
    return None