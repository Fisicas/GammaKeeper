"""Advisory count-domain QC models; never alter recorded counts or calibration."""
import math
import numpy as np

# Explicit channel-center inclusion, terminal channel excluded. These are broad
# monitoring windows, not isolated photopeak areas or absolute activity estimates.
WINDOWS = [('Pb-214 295 region',270.,320.),('Pb-214 352 region',325.,380.),
           ('609 / 583 region',550.,680.),('Bi-214 1120 region',1060.,1180.),
           ('Bi-214 1764 region',1680.,1850.),('Mixed 239 / 242',215.,265.),
           ('Tl-208 2615 region',2500.,2740.),('K-40 region',1320.,1600.)]


def peak_fit(counts,calibration,reference,low,high,blend=None):
    from scipy.optimize import minimize
    from scipy.special import ndtr
    ch=np.arange(len(counts));a0,a1,a2=calibration
    edges=a0+a1*(np.arange(len(counts)+1)-.5)+a2*(np.arange(len(counts)+1)-.5)**2
    centers=(edges[:-1]+edges[1:])/2
    use=(centers>=low)&(centers<=high)&(ch<len(ch)-1)
    y=np.asarray(counts,dtype=float)[use]
    result=dict(status='insufficient_information',reference_keV=reference,
                fit_range_keV=[low,high],parameter_bounds=dict(shift_keV=[-25,25],sigma_keV=[3,80]),
                model='Bin-integrated Gaussian peak(s) plus flat continuum, Poisson likelihood',
                blend_keV=blend,uncertainty='Conditional expected-information standard error; no calibration systematic included')
    if y.sum()<100 or len(y)<8: return result
    left,right=edges[:-1][use],edges[1:][use]
    width=right-left
    if np.any(width<=0): return dict(result,status='invalid_calibration')
    def prediction(p):
        amplitude,shift,sigma,background=np.exp(p[0]),p[1],np.exp(p[2]),np.exp(p[3])
        values=amplitude*(ndtr((right-reference-shift)/sigma)-ndtr((left-reference-shift)/sigma))+background*width
        if blend is not None:
            values+=np.exp(p[4])*(ndtr((right-blend-shift)/sigma)-ndtr((left-blend-shift)/sigma))
        return values
    def objective(p):
        mu=np.maximum(prediction(p),1e-20)
        return float(np.sum(mu-y*np.log(mu)))
    total=max(1.,y.sum())
    initial=[math.log(total*.5),0.,math.log(15. if reference<1000 else 30.),math.log(total/(high-low)*.5)]
    result['initial_guesses']=dict(peak_counts=total*.5,shift_keV=0.,sigma_keV=15. if reference<1000 else 30.,
                                   continuum_counts_per_keV=total/(high-low)*.5)
    bounds=[(-20,math.log(total*10+1)),(-25.,25.),(math.log(3.),math.log(80.)),(-20,math.log(total+1))]
    if blend is not None:initial.append(math.log(total*.1));bounds.append(bounds[0])
    fit=minimize(objective,initial,method='L-BFGS-B',bounds=bounds,options={'maxiter':500,'ftol':1e-12})
    if not fit.success:return dict(result,status='fit_failed',reason=str(fit.message))
    p=fit.x;mu=prediction(p)
    steps=np.array([1e-4]*len(p));steps[1]=1e-3
    jac=np.stack([(prediction(p+np.eye(len(p))[i]*steps[i])-prediction(p-np.eye(len(p))[i]*steps[i]))/(2*steps[i])
                  for i in range(len(p))],axis=1)
    fisher=jac.T@(jac/np.maximum(mu,1e-20)[:,None])
    try: covariance=np.linalg.inv(fisher)
    except np.linalg.LinAlgError:return result
    if not np.all(np.isfinite(covariance)) or covariance[0,0]<=0 or covariance[1,1]<=0:return result
    amplitude=np.exp(p[0]);area_sigma=amplitude*np.sqrt(covariance[0,0])
    deviance=2*float(np.sum(mu-y+np.where(y>0,y*np.log(np.maximum(y,1e-20)/mu),0)))
    dof=max(1,len(y)-len(p))
    on_boundary=any(abs(value-lo)<1e-3 or abs(value-hi)<1e-3 for value,(lo,hi) in zip(p,bounds))
    if amplitude/area_sigma<4 or on_boundary or deviance/dof>3:return dict(result,deviance_per_dof=deviance/dof)
    return dict(result,status='qualified_advisory',centroid_keV=reference+float(p[1]),
                centroid_sigma_keV=float(np.sqrt(covariance[1,1])),width_sigma_keV=float(np.exp(p[2])),
                peak_counts=float(amplitude),deviance_per_dof=deviance/dof,
                continuum_counts_per_keV=float(np.exp(p[3])),blend_counts=float(np.exp(p[4])) if blend is not None else None,
                caveat='609/583 components share shift and width; blend-model dependence remains.' if blend else
                       'Continuum shape and Gaussian response are assumptions; check residuals.')


def decay_fit(rows,window,seal_utc,ingrowth_exclusion_h=3.,background=None):
    from scipy.optimize import minimize
    selected=[r for r in rows if r['start_utc']>=seal_utc+ingrowth_exclusion_h*3600 and
              set(r['quality'])<={'normal','final_short_interval'}]
    result=dict(status='insufficient_information',window=window,ingrowth_exclusion_h=ingrowth_exclusion_h,
                time_zero_utc=seal_utc,model='Bin-integrated exponential plus constant background; joint Poisson counts',
                interpretation='Advisory apparent decay only; no activity, containment or physical qualification claim',
                exposure_assumption='Device exposure uniformly weights each UTC interval; uncalibrated dead time',
                background_assumption='Constant rate in the same geometry; reused background induces covariance',
                initial_guesses=None,exclusions='Non-normal timing/recovery flags and bins beginning before ingrowth cutoff')
    if len(selected)<8 or selected[-1]['end_utc']-selected[0]['start_utc']<86400:return result
    a=np.array([(r['start_utc']-seal_utc)/86400 for r in selected])
    b=np.array([(r['end_utc']-seal_utc)/86400 for r in selected])
    exposure=np.array([r['device_exposure_s'] for r in selected])
    y=np.array([r['window_counts'][window] for r in selected],dtype=float)
    if y.sum()<100 or np.any(exposure<=0) or np.any(b<=a):return result
    def means(p):
        amplitude,decay,bg=np.exp(p)
        average=np.exp(-decay*a)*(-np.expm1(-decay*(b-a)))/(decay*(b-a))
        return exposure*(amplitude*average+bg)
    def objective(p):
        mu=np.maximum(means(p),1e-20)
        value=np.sum(mu-y*np.log(mu))
        if background:
            n,t=background
            m=max(1e-20,np.exp(p[2])*t)
            value+=m-n*np.log(m)
        return float(value)
    rate=y/exposure
    bg0=(background[0]/background[1]) if background and background[1]>0 else max(1e-8,float(rate.min())*.5)
    amp0=max(1e-8,float(rate.max())-bg0)
    initial=np.log([amp0,math.log(2)/3.8222,max(1e-8,bg0)])
    result['initial_guesses']=dict(amplitude_cps=amp0,decay_per_day=math.log(2)/3.8222,background_cps=bg0)
    bounds=[(-25,math.log(max(1.,rate.max()*100))), (math.log(math.log(2)/100),math.log(math.log(2)/.1)),
            (-25,math.log(max(1.,rate.max()*100)))]
    fit=minimize(objective,initial,method='L-BFGS-B',bounds=bounds,options={'maxiter':500,'ftol':1e-12})
    if not fit.success:return dict(result,status='fit_failed')
    p=fit.x;step=1e-4;eye=np.eye(3)*step
    mu=means(p)
    deviance=2*float(np.sum(mu-y+np.where(y>0,y*np.log(np.maximum(y,1e-20)/mu),0)))
    result['deviance_per_dof']=deviance/max(1,len(y)-3)
    if result['deviance_per_dof']>3:return dict(result,status='model_requires_review')
    hessian=np.empty((3,3))
    for i in range(3):
        for j in range(3):
            hessian[i,j]=(objective(p+eye[i]+eye[j])-objective(p+eye[i]-eye[j])-
                          objective(p-eye[i]+eye[j])+objective(p-eye[i]-eye[j]))/(4*step**2)
    try:covariance=np.linalg.inv(hessian)
    except np.linalg.LinAlgError:return result
    if np.any(np.linalg.eigvalsh(hessian)<=0) or not np.all(np.isfinite(covariance)):return result
    half=math.log(2)/np.exp(p[1]);sigma=half*np.sqrt(covariance[1,1])
    if sigma/half>.5 or any(min(abs(value-lo),abs(value-hi))<1e-3 for value,(lo,hi) in zip(p,bounds)):
        return dict(result,status='poorly_identified')
    return dict(result,status='qualified_advisory',half_life_d=float(half),half_life_sigma_d=float(sigma),
                amplitude_cps=float(np.exp(p[0])),background_cps=float(np.exp(p[2])),bins=len(selected),
                included_start_utc=selected[0]['start_utc'],included_end_utc=selected[-1]['end_utc'],
                uncertainty='Observed-curvature standard error conditional on exponential, fixed geometry and background model',
                nuclear_reference=dict(half_life_d=3.8222,standard_uncertainty_d=.0009,
                    source='https://www.nndc.bnl.gov/ensnds/222/Rn/adopted.pdf',evaluation='ENSDF September 2023'))
