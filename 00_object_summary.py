#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This script takes in an SDSS-V catalog ID and generates end-to-end
summary plots of the object. Currently includes:
    SDSS-V spectrum and analysis
    Gaia EDR3 CMD

Could be added:
    Vizier SED
    ZTF photometry
    
"""


import numpy as np
import matplotlib.pyplot as plt
import sys
from astropy.io import fits
import os
from astropy.table import Table
from tqdm import tqdm
import glob
import copy
import scipy
import lmfit
from astropy import units as u
from astropy.timeseries import LombScargle
from matplotlib.colors import LogNorm
from astropy import constants as c
from astroquery.gaia import Gaia
import corner 
import pandas as pd
import emcee
from PIL import Image
import shutil

# Define paths. This can be changed for a different system

corv_path = '/Users/vedantchandra/0_research/corv/'
WDmodels_path =  '/Users/vedantchandra/0_research/'
lookuptable = ('/Users/vedantchandra/0_research/'
               '13_sdss5/06_wd_rv_variability/'
               'tables/lookuptable.fits')
rvtable_path = ('/Users/vedantchandra/0_research/'
               '13_sdss5/06_wd_rv_variability/'
               'tables/rvfits_fullspec.fits')
exppath = '/Users/vedantchandra/0_research/13_sdss5/06_wd_rv_variability/'
plt.style.use('vedant')
output_dir = ('/Users/vedantchandra/0_research/13_sdss5/06_wd_rv_variability/'
              'fig/')

gcns = Table.read('/Users/vedantchandra/0_research/'
                  '09_gemini_wd/shen_d62/misc_files/GCNS_cat.fits')

# Path-specific imports

sys.path.append(corv_path)
import corv
sys.path.append(WDmodels_path)
import WD_models_master
lutable = Table.read(lookuptable)
evol_model = WD_models_master.load_model('Bedard2020', 'Bedard2020', 
                                         'Bedard2020', 'H',
                                   HR_bands=('bp3-rp3', 'G3'))
rvtable = Table.read(rvtable_path)
plt.ioff()


#%% Convenience Functions

balmer = dict(a = 6564.61, b = 4862.68, g = 4341.68, d = 4102.89)
windows = dict(a = 200, b = 200, g = 85, d = 70)
edges = dict(a = 50, b = 50, g = 20, d = 20)
lines = ['d', 'g', 'b', 'a']

def make_coadd(exps):
    fls = [];
    ivars = [];

    for nn in range(len(exps)):
        exp = exps[nn]

        fls.append(np.interp(loglamgrid, exp['logwl'], (exp['fl'])))
        ivars.append(np.interp(loglamgrid, exp['logwl'], (exp['ivar'])))

    ivars = np.array(ivars)
    mask = ivars == 0
    ivars[mask] = 1 # dummy
    variances = 1 / ivars
    variance = np.sum(variances, axis = 0) / len(variances)**2
    ivar = 1 / variance
    
    fl = np.median(fls, axis = 0)
    smask = (mask).all(axis = 0)
    ivar[smask] = 0
    
    return fl, ivar

w1 = 3600
w2 = 9000

loglamgrid = np.linspace(np.log10(w1), np.log10(w2), 6500) # grid for co-add wavelengths
lamgrid = 10**loglamgrid
break_wl = 5900 # transition from red to blue

def get_exposure(bf, rf):
    exp = {};
    with fits.open(bf) as f:
        
        bwl = f[3].data
        bfl = f[0].data
        bivar = f[1].data
        bdisp = f[4].data
        bsky = f[5].data
        
        exp['tai_beg'] = f[0].header['TAI-BEG']
        exp['airmass'] = f[0].header['AIRMASS']
        exp['vhelio'] = f[0].header['HELIO_RV']
        exp['az'] = f[0].header['AZ']
        exp['alt'] = f[0].header['ALT']
        exp['mjd'] = f[0].header['MJD']
        exp['edr3_source'] = int(f[0].header['G_EDR3'])
        exp['RA'] = f[0].header['RA']
        exp['DEC'] = f[0].header['DEC']

    with fits.open(rf) as f:
        
        rwl = f[3].data
        rfl = f[0].data
        rivar = f[1].data
        rdisp = f[4].data
        rsky = f[5].data
        
    rsel = rwl > np.log10(break_wl)
    bsel = bwl < np.log10(break_wl)
        
    exp['logwl'] = np.concatenate((bwl[bsel], rwl[rsel]))
    exp['fl'] = np.concatenate((bfl[bsel], rfl[rsel]))
    exp['ivar'] = np.concatenate((bivar[bsel], rivar[rsel]))
    exp['wdisp'] = np.concatenate((bdisp[bsel], rdisp[rsel]))
    exp['sky'] = np.concatenate((bsky[bsel], rsky[rsel]))
        
    return exp

break_wl = 6000

def get_exposures(catalogid):
    seltable = lutable[lutable['catalogid'] == '0' + str(catalogid)]
    exps = [];
    for row in seltable:
        exps.append(get_exposure(exppath  + row['bluefiles'],
                                 exppath + row['redfiles']))
    return exps

def get_radec(catalogid):
    seltable = lutable[lutable['catalogid'] == '0' + str(catalogid)]
    with fits.open(seltable[0]['bluefiles']) as f:
        ra,dec = f[0].header['RA'], f[0].header['DEC']
    return ra,dec

#%% Get Data

Gaia.MAIN_GAIA_TABLE = "gaiaedr3.gaia_source"

# SDSS-V and RVs

try:
    cid = int(sys.argv[1])
except:
    raise OSError('please pass a catalog ID as an argument!')
    
# cid = 4592943353 # for testing purposes

table = rvtable[rvtable['catalogid'] == cid]

exps = get_exposures(cid)

# Gaia

source_id = exps[0]['edr3_source']

job = Gaia.launch_job("select * "
                      "from gaiaedr3.gaia_source "
                      "where (source_id = %s) " % str(source_id))

gaia = job.get_results()[0]

job = Gaia.launch_job("select * "
                      "from external.gaiaedr3_distance "
                      "where (source_id = %s) " % str(source_id))

bjdist = job.get_results()[0]

# Make output directory

outdir = output_dir + '%s/' % str(source_id)
outroot = outdir + '%s' % str(source_id) + '_'
try:
    os.mkdir(outdir)
except:
    print('dir exists, clearing')
    shutil.rmtree(outdir)
    os.mkdir(outdir)
    
#%% Analyze SDSS-V

# Fit co-add for template

stack_fl, stack_ivar = make_coadd(exps)
corvmodel = corv.models.make_koester_model()
pres, rres, _ = corv.fit.fit_corv(lamgrid, stack_fl, stack_ivar, corvmodel)

# Fit individual exposures for RVs

for exp in tqdm(exps):
    wl = 10**exp['logwl']
    sel = (wl > w1) & (wl < w2)
    resi,_ = corv.fit.fit_rv(wl[sel], exp['fl'][sel], 
                             exp['ivar'][sel], corvmodel, pres.params)
    exp['rv'] = resi.params['RV'].value
    exp['e_rv'] = resi.params['RV'].stderr

def get_expdata(exps):
    keys = exps[0].keys()
    expdata = {key: [] for key in keys}
    for exp in exps:
        for key in keys:
            datum = exp[key]
            if not isinstance(datum, np.ndarray):
                expdata[key].append(datum)
                
    for key in keys:
        if len(expdata[key]) == 0:
            expdata.pop(key)
                
    return Table(expdata)

daysec = (1 * u.day).to(u.second).value

data = get_expdata(exps)

data['taibeg_mjd'] = data['tai_beg'] / daysec

# Re-make coadd with Doppler-shifted exposures

exps_corrected = copy.deepcopy(exps);

for jj,exp in enumerate(exps_corrected):
    
    exp_rv = exp['rv']
    wl = 10**exp['logwl']
    
    fl_corr = corv.utils.doppler_shift(wl, exp['fl'], -exp_rv)
    ivar_corr = corv.utils.doppler_shift(wl, exp['ivar'], -exp_rv)
    
    exps_corrected[jj]['fl'] = fl_corr
    exps_corrected[jj]['ivar'] = ivar_corr

stack_fl_corr, stack_ivar_corr = make_coadd(exps_corrected)

ra = gaia['ra']
dec = gaia['dec']

#%%
# Plot Coadd

from astropy.coordinates import SkyCoord

plt.figure(figsize = (10, 6))

plt.plot(lamgrid, stack_fl_corr, 'k')

plt.xlabel('Wavelength ($\AA$)')
plt.ylabel('Flux ($10^{-17}\,erg\,cm^{-2}\,s^{-1}\,\AA^{-1}$)')

coord = SkyCoord(ra = gaia['ra'] * u.deg, dec = gaia['dec'] * u.deg)
jcoord = coord.to_string('hmsdms', precision = 1).replace('h', 
                    '').replace('m', '').replace('d', 
                        '').replace('s','').replace(' ', '')

strt = 0.95
gap = 0.07

plt.text(0.97, strt, r'Gaia EDR3 %i' % gaia['source_id'], ha = 'right', va = 'top',
            transform = plt.gca().transAxes, fontsize = 18)

plt.text(0.97, strt - gap, r'SDSS J%s' % (jcoord), ha = 'right', va = 'top',
            transform = plt.gca().transAxes, fontsize = 18)

plt.text(0.97, strt - 2*gap, r'CID %i' % cid, ha = 'right', va = 'top',
            transform = plt.gca().transAxes, fontsize = 18)

plt.text(0.97, strt - 3*gap, r'[%.5f, %.5f]' % (ra,dec), ha = 'right', va = 'top',
            transform = plt.gca().transAxes, fontsize = 18)

plt.text(0.97, strt - 4*gap, r'G = %.1f' % (gaia['phot_g_mean_mag']), 
         ha = 'right', va = 'top',
            transform = plt.gca().transAxes, fontsize = 18)

plt.savefig(outroot + '001_spec_coadd.png')
plt.show(block = False)
#%%
# Re-fit template to corrected coadd, re-fit RVs

corvmodel = corv.models.make_koester_model()

pres, rres, _ = corv.fit.fit_corv(lamgrid, stack_fl_corr, 
                                  stack_ivar_corr, corvmodel)

for exp in tqdm(exps):
    wl = 10**exp['logwl']
    sel = (wl > w1) & (wl < w2)
    resi,_ = corv.fit.fit_rv(wl[sel], exp['fl'][sel], exp['ivar'][sel], 
                             corvmodel, pres.params)
    exp['rv'] = resi.params['RV'].value
    exp['e_rv'] = resi.params['RV'].stderr

expdata_corr = get_expdata(exps)

expdata_corr['taibeg_mjd'] = expdata_corr['tai_beg'] / daysec

mjd = np.array(expdata_corr['taibeg_mjd'])
rv = np.array(expdata_corr['rv'])
e_rv = np.array(expdata_corr['e_rv'])

#%% Fit Atmospheric Parameters to rv-corrected spectrum

pres, rres, _ = corv.fit.fit_corv(lamgrid, stack_fl_corr, 
                                  stack_ivar_corr, corvmodel,
                                  iter_teff = True)

allnames = ['n', 'z', 'e', 'd', 'g', 'b', 'a']

allcentres = dict(a = 6564.61, b = 4862.68, g = 4341.68, d = 4102.89,
                 e = 3971.20, z = 3890.12, n = 3835.5,
             t = 3799.5)
allwindows = dict(a = 100, b = 100, g = 85, d = 70, e = 30,
                  z = 25, n = 15, t = 10)

alledges = dict(a = 25, b = 25, g = 20, d = 20, 
                e = 5, z = 5, n = 5, t = 4)

fullmodel = corv.models.make_koester_model(1, 
                                           allcentres,
                                            allwindows,
                                            alledges,
                                            allnames)

corv.utils.lineplot(lamgrid, stack_fl_corr, stack_ivar_corr,
                    fullmodel, pres.params, figsize = (6, 7),
                    gap = 0.35)

plt.savefig(outroot + '002_spec_gfp.png')
plt.show(block = False)
#%% Gaia CMD

gclean = (
    (gcns['PARALLAX'] / gcns['PARALLAX_ERROR'] > 10)

)

gcns['g_abs'] = gcns['PHOT_G_MEAN_MAG'] - 5 * np.log10(1000 / gcns['PARALLAX']) + 5
gcns['bp_rp'] = gcns['PHOT_BP_MEAN_MAG'] - gcns['PHOT_RP_MEAN_MAG']

gclean = (
    (gcns['PARALLAX'] / gcns['PARALLAX_ERROR'] > 10)&
    (~np.isnan(gcns['g_abs'])) & 
    (~np.isnan(gcns['bp_rp']))  & 
    (gcns['PARALLAX_ERROR'] < .1)

)

g_abs = gaia['phot_g_mean_mag'] - 5 * np.log10(bjdist['r_med_geo']) + 5
g_abs_lo = gaia['phot_g_mean_mag'] - 5 * np.log10(bjdist['r_hi_geo']) + 5
g_abs_hi = gaia['phot_g_mean_mag'] - 5 * np.log10(bjdist['r_lo_geo']) + 5

bp_rp = gaia['bp_rp']

yerr = np.array([[g_abs - g_abs_lo, g_abs_hi - g_abs]]).T

gaia_teff = 10**evol_model['HR_to_logteff'](bp_rp, g_abs)
gaia_logg = evol_model['HR_to_logg'](bp_rp, g_abs)

e_gaia_teff = np.abs(10**evol_model['HR_to_logteff'](bp_rp, g_abs_lo) - 
                     10**evol_model['HR_to_logteff'](bp_rp, g_abs_hi)) / 2

e_gaia_logg = np.abs(evol_model['HR_to_logg'](bp_rp, g_abs_lo) - 
                     evol_model['HR_to_logg'](bp_rp, g_abs_hi)) / 2

# Cooling tracks -- add He models here

logteff_msun_to_mag = WD_models_master.interp_xy_z_func(evol_model['logteff'], 
                                                        evol_model['mass_array'],
                                                         evol_model['Mag'])

logteff_msun_to_color = WD_models_master.interp_xy_z_func(evol_model['logteff'], 
                                                          evol_model['mass_array'],
                                                         evol_model['color'])

logteffs = np.linspace(np.log10(55000), np.log10(3000), 100)

track06_c = logteff_msun_to_color(logteffs, 0.6)
track06_m = logteff_msun_to_mag(logteffs, 0.6)
track02_m = logteff_msun_to_mag(logteffs, 0.2)

track0606_m = -2.5 * np.log10(2*10**(-0.4 * track06_m))
track0602_m = -2.5 * np.log10(10**(-0.4 * track06_m) + 10**(-0.4 * track02_m))

if not np.isnan(gaia_teff):
    gaia_teff = int(gaia_teff)
    e_gaia_teff = int(e_gaia_teff)


plt.figure(figsize = (8, 8))

plt.title('EDR3 %s' % gaia['source_id'])

plt.hist2d(gcns['bp_rp'][gclean], gcns['g_abs'][gclean],
          bins = 250, cmap = 'Greys', norm = LogNorm(), rasterized = True);

plt.errorbar(bp_rp, g_abs, marker = '*', mfc = 'tab:red', mec = 'k', lw = 2, markersize = 20,
            yerr = yerr, label = 'Candidate', mew = 1.5, ecolor = 'k', zorder = 25)

plt.text(0.95, 0.7, 'CMD & B20:', ha = 'right', va = 'top', 
        transform = plt.gca().transAxes)

plt.text(0.95, 0.65, '$T_{eff} = %i \pm %i$ K' % (gaia_teff, e_gaia_teff), ha = 'right', va = 'top', 
        transform = plt.gca().transAxes)
plt.text(0.95, 0.6, '$\log{g} = %.1f \pm %.1f$ dex' % (gaia_logg, e_gaia_logg), ha = 'right', va = 'top', 
        transform = plt.gca().transAxes)

plt.plot(track06_c, track06_m, color = 'dodgerblue', zorder = 10, label = r'$0.6\,M_\odot$',
        lw = 2)

plt.plot(track06_c, track0606_m, color = 'tab:red', zorder = 10, label = r'$0.6\,M_\odot + 0.6\,M_\odot$',
        lw = 2)

plt.plot(track06_c, track0602_m, color = 'tab:orange', zorder = 10, label = r'$0.6\,M_\odot + 0.2\,M_\odot$',
        lw = 2)


plt.gca().invert_yaxis()

plt.legend(loc = 'upper right', framealpha = 1)

plt.xlabel('$G_{BP} - G_{RP}$')
plt.ylabel('$M_G$')

plt.xlim(-1, 6)

plt.savefig(outroot + '003_cmd.png')
plt.show(block = False)

#%% Radial Velocities

w = 1 / e_rv**2
mu_rv = np.sum(rv * w) / np.sum(w)
redchi = np.sum(((rv - mu_rv) / e_rv)**2) / (len(rv) - 1)
drvmax = np.max(rv) - np.min(rv)

plt.figure(figsize = (8, 8)) 

plt.errorbar(mjd, rv, e_rv,
           linestyle = 'none', marker = 'o', color = 'k')

plt.text(0.95, 0.97, '$\chi_r^2 = %.1f$' % (redchi), ha = 'right', va = 'top', 
        transform = plt.gca().transAxes)

plt.text(0.95, 0.92, '$\Delta\,RV_{max} = %i\, kms^{-1}$' % (drvmax), ha = 'right', va = 'top', 
        transform = plt.gca().transAxes)

plt.xlabel('Time (days)')
plt.ylabel('RV (kms$^{-1}$)')

plt.ylim(np.min(rv) - 150, np.max(rv) + 150)

plt.savefig(outroot + '004_rvs.png')
plt.show(block = False)

plt.figure(figsize = (8, 8))

ls = LombScargle(mjd * u.day, rv, e_rv, nterms = 1)
    
freq, power = ls.autopower(minimum_frequency = 2 / u.day, 
                           maximum_frequency = 30 / u.day,
                          normalization = 'psd', samples_per_peak = 100)

bestf = freq[np.argmax(power)]
bestp = (1/bestf)
bestp_hr = bestp.to(u.hour).value

plt.plot(freq, power, color = 'k')
plt.xlabel('Cycles / day')
plt.title('$P_{best}$ = %.5f hr' % (bestp_hr))
plt.ylabel('Power')

plt.savefig(outroot + '005_lspgram.png')
plt.show(block = False)

phase = ((mjd) % bestp.value) / bestp.value

plt.figure(figsize = (8, 8)) 

plt.errorbar(np.concatenate((phase, phase + 1)), 
             np.concatenate((rv, rv)), 
             np.concatenate((e_rv, e_rv)),
           linestyle = 'none', marker = 'o', color = 'k')

plt.xlabel('Phase (P = %.5f hr)' % bestp_hr)
plt.ylabel('RV (kms$^{-1}$)')
plt.xlim(-0.1, 2.1)

plt.savefig(outroot + '006_rvs_pp.png')
plt.show(block = False)

#%% Phase-folded Balmer
pidx = np.argsort(phase)
balmerlist = [4341.68, 4862.68, 6564.61]
names = [r'H$\gamma$ ($\lambda 4341\,\AA$)', r'H$\beta$ ($\lambda 4862\,\AA$)',
         r'H$\alpha$ ($\lambda 6564\,\AA$)']
f,axs = plt.subplots(1,3,figsize = (10, 5))

for jj,line in enumerate(balmerlist):      
    ctr = 0;
    fls = [];


    for exp in np.array(exps)[pidx]:

        wl, fl,ivar = 10**exp['logwl'], exp['fl'], exp['ivar']
        wl, fl, ivar = corv.utils.crrej(wl, fl, ivar, nsig = 3.5, plot = False)

        fl = np.interp(lamgrid, wl, fl)

        vgap = 1000

        nwl, nfl, _ = corv.utils.cont_norm_line(lamgrid, fl, fl, line, 
                                        int(line * 1e3 * vgap/c.c.value), 6)

        vel = 1e-3 * c.c.value * (nwl - line) / nwl

        ctr += 1        
        fls.append(nfl)

    fls = np.array(fls)
    
    plt.sca(axs[jj])
    
    plt.pcolormesh(vel, phase[pidx], fls, vmin = 0.5, vmax = 1, cmap = 'viridis_r',
                      shading = 'nearest')
        
    plt.gca().invert_yaxis()
    
    if jj == 0:
        plt.ylabel('Phase')
    else:
        plt.gca().set_yticklabels([])
        
    if jj == 1:
        plt.xlabel('$\Delta$ RV ($kms^{-1}$)')
        
    plt.title(names[jj])
        
plt.subplots_adjust(wspace = 0.05)

plt.savefig(outroot + '007_balmer_pp.png')
plt.show(block = False)

#%% Fit Folded RVs

pgrid = np.linspace(0, 1, 1000)
time_hr = (mjd*u.day).to(u.hour).value

def rvmodel(ph, params):
    rvs = params['gamma'] + params['K'] * np.sin(2 * np.pi * ph + params['phi'])
    return rvs

def residual(params):
    ph = (time_hr % params['P']) / params['P']
    model = rvmodel(ph, params) 
    resid = (rv - model) / e_rv
    return resid

params = lmfit.Parameters()
params.add('gamma', value = -5)
params.add('K', value = 200, min = 0)
params.add('phi', value = 0, min = -2 * np.pi, max = 2 * np.pi)
params.add('P', min = 1, max = 10, value = bestp_hr, vary =  False)
params.add('rvjit', min = 0, max = 250, value = 10, vary =  False)

res = lmfit.minimize(residual, params)

plt.figure(figsize = (10, 7)) 

phase = (time_hr % res.params['P']) / res.params['P']

plt.errorbar(phase, rv, e_rv,
           linestyle = 'none', marker = 'o', color = 'k')

plt.plot(pgrid, rvmodel(pgrid, params), 'C0', label = 'Initial')
plt.plot(pgrid, rvmodel(pgrid, res.params), 'C3', label = 'Fit')

plt.legend()
plt.xlabel('Phase (P = %.5f hr)' % bestp_hr)
plt.ylabel('RV (km/s)')
plt.xlim(-0.1, 1.1)
plt.savefig(outroot + 'init_rvfit.png')
plt.show(block = False)

#%% Full RV model fit with jitter term

p_init = [];
e_p_init = [];


pnames = ['gamma', 'K', 'phi']
for param in pnames:
    p_init.append(res.params[param].value)
    e_p_init.append(res.params[param].stderr)

pnames.append('s')
p_init.append(25)
e_p_init.append(1)

def loglik(theta):
    model = rvmodel(phase, dict(P = bestp_hr, gamma = theta[0],
                               K = theta[1], phi = theta[2]))
    
    sigma2 = e_rv ** 2 + theta[3] ** 2
    return -0.5 * np.sum((rv - model)**2 / sigma2 + np.log(sigma2))

def logprior(theta):
    if theta[1] < 0:
        return -np.Inf
    if theta[3] < 0:
        return - np.Inf
    
    return 0.0

def log_probability(theta):
    lp = logprior(theta)
    if not np.isfinite(lp):
        return -np.inf
    return lp + loglik(theta)

nll = lambda *args: -log_probability(*args)
initial = np.array([res.params['gamma'].value, res.params['K'].value, 
                    res.params['phi'].value, 10])
soln = scipy.optimize.minimize(nll, initial)

pos = soln.x + 1e-3 * soln.x * np.random.randn(250, 4)
nwalkers, ndim = pos.shape

sampler = emcee.EnsembleSampler(nwalkers, ndim, log_probability)
sampler.run_mcmc(pos, 1000, progress=True);

flatchain = sampler.get_chain(discard = 500, thin = 5, flat = True)

f = corner.corner(flatchain, labels = pnames, show_titles = True)
plt.savefig(outroot + 'rvfit_corner.pdf')
plt.show(block = False)

flatchain = pd.DataFrame(flatchain, columns = pnames)

plt.figure(figsize = (10, 7)) 

plt.errorbar(phase, rv, e_rv,
           linestyle = 'none', marker = 'o', color = 'k', label = 'Data')

ncurve = 100

for N in range(ncurve):
    idx = np.random.randint(0, len(flatchain))
    row = flatchain.iloc[idx]
    
    model = rvmodel(pgrid, row)
    
    if N == 0:
        lab = 'Posterior Models'
    else:
        lab = None
    
    plt.plot(pgrid, model, 'r', lw = 1, alpha = 0.1, label = lab)
    
plt.xlabel('Phase (P = %.5f hr)' % bestp_hr)
plt.ylabel('RV (kms$^{-1}$)')
plt.xlim(-0.1, 1.1)
leg = plt.legend()
for lh in leg.legendHandles: 
    lh.set_alpha(1)

plt.savefig(outroot + '008_fit_rvs.png')
plt.show(block = False)

#%% Analytic Calculations

logteff_logg_to_msun = WD_models_master.interp_xy_z_func(evol_model['logteff'], evol_model['logg'],
                                                        evol_model['mass_array'])

m1_spec = logteff_logg_to_msun(np.log10(pres.params['teff'].value), 
                               pres.params['logg'].value)
m1_phot = evol_model['HR_to_mass'](bp_rp, g_abs)

if np.isnan(m1_spec) and pres.params['logg'] <= 6.5:
    m1_spec = 0.2
    print('off B20 grid, assuming M1 = 0.2')

bestfit = flatchain.mean()
e_bestfit = flatchain.std()

def mass_func(P, K):
    P = np.array(P) * u.hour
    K = np.array(K) * u.km / u.s
    
    num = P * K**3
    denom = 2 * np.pi * c.G
    
    return (num/denom).to(u.Msun).value

def get_m2(f, inc, M1):
    M1 = M1 * u.Msun
    f = f * u.Msun
    inc = inc * u.degree
    
    def resid(prms):
        M2 = prms[0] * u.Msun
        f_pred = ((M2 * np.sin(inc))**3 / (M1 + M2)**2).to(u.Msun)
        return ((f_pred - f).value)**2
    
    soln = scipy.optimize.minimize(resid, x0 = [0.5], bounds = [(0, 500)])
    
    return soln.x[0]

f = mass_func(np.repeat(bestp_hr, len(flatchain)), flatchain['K'])

plt.figure(figsize = (7, 7))

plt.hist(f, density = True, color = 'tab:blue', histtype = 'step', lw = 3, bins = 1000,
        label = '$f(M_2)$', range = (0, 1));
plt.xlabel(r'M_2 $(M_\odot)$')
plt.ylabel('Density')

plt.hist(f**(1/3) * m1_spec**(2/3), density = True, color = 'tab:orange', 
         histtype = 'step', lw = 3, bins = 1000, label = '$f(M_2)^{1/3} M_1^{2/3}$', range = (0, 1.4))

plt.title('$M_1 (spec) = %.2f\,M_\odot$' % m1_spec)

plt.legend()

plt.savefig(outroot + '009_fm_pdf.png')
plt.show(block = False)

#%% M1 and inclination vs M2

N1 = 25
N2 = 25

m1 = np.linspace(0.1, 1, N1)
inc = np.linspace(5, 90, N2)

M2s = np.zeros((N1,N2));

for ii in tqdm(range(N1)):
    for jj in range(N2):
        M2s[ii,jj] = (get_m2(m1[ii], inc[jj], f.mean()))
        
M2s = np.array(M2s)

plt.figure(figsize = (8, 6))

plt.pcolormesh(m1, inc, M2s.T, cmap = 'Spectral_r', shading = 'gouraud', vmax = 1.45);

plt.xlabel('$M_1\ (M_\odot)$')
plt.ylabel('Inclination (degree)')

cbar = plt.colorbar()
cbar.ax.set_ylabel('$M_2\ (M_\odot)$')
plt.axvline(m1_spec, color = 'k', linestyle = '--', lw = 2)

plt.tight_layout()

plt.savefig(outroot + '010_m1_m2_inc.png')
plt.show(block = False)

#%% Assume M1 = M1_spec and inclination distribution

m1_assumed = m1_spec

sinincs = np.linspace(0.1, 1, len(flatchain))
sinincs = np.random.choice(sinincs, size = len(flatchain), replace = True, p = sinincs**2 / np.sum(sinincs**2))

f = mass_func(np.repeat(bestp_hr, len(flatchain)), flatchain['K'])

m2_samples = np.array([get_m2(f[ii], np.arcsin(sinincs[ii]) * 180 / np.pi,
                     m1_assumed) for 
                      ii in tqdm(range(1000))])

plt.figure(figsize = (5, 5))

plt.subplot(211)
plt.hist(m2_samples, range = (0, 1.4), density = True, bins = 25, histtype = 'step', lw = 3, color = 'k');
plt.xlabel('$M_2$ $(M_\odot)$')
plt.ylabel('Density')
plt.title(r'$Pr(i) \propto \sin^2(i)$, $M_1 = %.2f M_\odot$' % m1_spec);

tot_samples = m2_samples + m1_spec

plt.subplot(212)

plt.hist(tot_samples, range = (0, 1.4), bins = 25, density = True, histtype = 'step', lw = 3, color = 'k');
plt.xlabel('$M_1 + M_2$ $(M_\odot)$')
#plt.ylabel('Density')

P_1m = np.sum(tot_samples > 1) / len(tot_samples)
P_ch = np.sum(tot_samples > 1.4) / len(tot_samples)

plt.title(r'$Pr(M_t > 1 M_\odot) \approx %.2f$' % P_1m)

plt.tight_layout()

plt.savefig(outroot + '011_prob_m2.png')
plt.show(block = False)

#%% Combine plots into summary page

def append_images(images, direction='horizontal',
                  bg_color=(255,255,255), aligment='center'):
    """
    Appends images in horizontal/vertical direction.

    Args:
        images: List of PIL images
        direction: direction of concatenation, 'horizontal' or 'vertical'
        bg_color: Background color (default: white)
        aligment: alignment mode if images need padding;
           'left', 'right', 'top', 'bottom', or 'center'

    Returns:
        Concatenated image as a new PIL image object.
    """
    widths, heights = zip(*(i.size for i in images))

    if direction=='horizontal':
        new_width = sum(widths)
        new_height = max(heights)
    else:
        new_width = max(widths)
        new_height = sum(heights)

    new_im = Image.new('RGB', (new_width, new_height), color=bg_color)


    offset = 0
    for im in images:
        if direction=='horizontal':
            y = 0
            if aligment == 'center':
                y = int((new_height - im.size[1])/2)
            elif aligment == 'bottom':
                y = new_height - im.size[1]
            new_im.paste(im, (offset, y))
            offset += im.size[0]
        else:
            x = 0
            if aligment == 'center':
                x = int((new_width - im.size[0])/2)
            elif aligment == 'right':
                x = new_width - im.size[0]
            new_im.paste(im, (x, offset))
            offset += im.size[1]

    return new_im

files = np.sort(glob.glob(outroot + '0*.png'))
fidx = np.array([int(os.path.basename(file).split('_')[1]) for file in files])

# Spec and CMD

ims = [];
for file in files[:3]:
    ims.append(Image.open(file))
im1 = append_images(ims, 'horizontal')

# RVs

ims = []
for file in files[3:6]:
    ims.append(Image.open(file))
im2 = append_images(ims, 'horizontal')

# Balmer and fit

ims = []
for file in files[6:8]:
    ims.append(Image.open(file))
im3 = append_images(ims, 'horizontal')

# analytic

ims = []
for file in files[8:11]:
    ims.append(Image.open(file))
im4 = append_images(ims, 'horizontal')

merged = append_images([im1, im2, im3, im4], 'vertical')

basewidth = 3000 # sets resolution

wpercent = (basewidth/float(merged.size[0]))
hsize = int((float(merged.size[1])*float(wpercent)))
img = merged.resize((basewidth,hsize), Image.ANTIALIAS)
img.save(outroot + 'summary.jpg')